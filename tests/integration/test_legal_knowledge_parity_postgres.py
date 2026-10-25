"""Synthetic legal-knowledge retrieval through canonical Judit/worker/publication."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app import providers
from app.claim_evidence import build_material_claims
from app.db import create_pool
from app.generation_authorization import PROPOSED_MODEL
from app.migrations import migrate
from app.tpu_glossary import load_legal_knowledge
from app.worker import Worker, WorkerSettings
from scripts.evaluate_writing_parity import evaluate as evaluate_writing_parity
from tests.test_generation_authorization import configure_approval
from tests.test_parser_parity import source

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")


def _writing_payload(process_json: dict, steps: list[dict]) -> dict:
    """Simulate a generator that cites raw movements instead of unsupported summaries."""
    timeline = []
    for step in steps:
        occurred_at = datetime.fromisoformat(step["occurred_at"].replace("Z", "+00:00"))
        timeline.append(
            {
                "text": f"Em {occurred_at:%d/%m/%Y}. {step['text']}",
                "refs": [step["evidence_ref"]],
            }
        )
    class_entry = next(item for item in process_json["legal_knowledge"] if item["id"] == "classe-436")
    subject_entry = next(item for item in process_json["legal_knowledge"] if item["id"] == "assunto-14033")
    class_quote = "3º O Juizado Especial Cível tem competência para conciliação, processo e julgamento das causas cíveis de menor complexidade, inclusive as de valor até quarenta vezes o salário mínimo."
    subject_quote = "VI - a efetiva prevenção e reparação de danos patrimoniais e morais, individuais, coletivos e difusos"
    intimation_entry = next(item for item in process_json["legal_knowledge"] if item["id"] == "mov-intimacao")
    intimation_quote = "Intimação é o ato pelo qual se dá ciência a alguém dos atos e dos termos do processo."
    assert class_quote in class_entry["definition"]
    assert subject_quote in subject_entry["definition"]
    assert intimation_quote in intimation_entry["definition"]
    parties = "; ".join(
        f"{party['name']} (polo cadastrado: {'ativo' if party.get('side') == 'Active' else 'passivo'})"
        for party in process_json["parties"]
    )
    distribution = next(item for item in steps if "DISTRIBUÍDO" in item["text"])
    hearing = next(item for item in steps if "scheduled_hearing" in item["source_temporal_facts"])
    synthesis = (
        f"Contexto do processo: {process_json['class_name']}. "
        f"{class_quote} {subject_quote}. {intimation_quote} "
        f"Andamento processual: {distribution['text']}. {hearing['text']}"
    )
    payload = {
        "synthesis": synthesis,
        "timeline": [item["text"] for item in timeline],
        "current_status": steps[-1]["text"],
        "attention": [steps[-1]["text"]],
        "decisions": [], "deadlines": [], "related_processes": [], "attachments": [],
    }
    refs = [process_json["evidence_ref"], *[step["evidence_ref"] for step in steps]]
    payload["claims"] = build_material_claims(payload, evidence_refs=refs)
    for index, item in enumerate(timeline):
        next(claim for claim in payload["claims"] if claim["claim_id"] == f"timeline:{index}")["evidence_refs"] = item["refs"]
    next(claim for claim in payload["claims"] if claim["claim_id"] == "synthesis")["evidence_refs"] = refs
    next(claim for claim in payload["claims"] if claim["claim_id"] == "current_status")["evidence_refs"] = [steps[-1]["evidence_ref"]]
    next(claim for claim in payload["claims"] if claim["claim_id"] == "attention:0")["evidence_refs"] = [steps[-1]["evidence_ref"]]
    return payload


async def test_legal_knowledge_flows_through_authorized_pipeline_with_provenance(monkeypatch, tmp_path):
    assert URL
    await migrate(URL)
    pool = await create_pool(URL, min_size=2, max_size=6)
    tenant, other = uuid4(), uuid4()
    monkeypatch.setattr(app.state, "pool", pool, raising=False)
    monkeypatch.setattr(app.state, "bearer_tokens", {"m01-owner": tenant, "m01-other": other}, raising=False)
    monkeypatch.setattr(app.state, "webhook_tenant_id", tenant, raising=False)
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "m01-synthetic-webhook")
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "m01-synthetic-no-provider-credential")
    monkeypatch.setenv("DATAJUD_ENABLED", "false")
    page = source()
    item = page["page_data"][0]
    request_id = f"m01-{uuid4()}"
    item["request_id"] = request_id
    item["response_id"] = f"response-{uuid4()}"
    code = item["response_data"]["code"]
    outbound = []

    async def create(**request):
        outbound.append(request)
        prompt = request["messages"][0]["content"]
        assert "source_parser" not in prompt and "Ignore a área" not in prompt
        assert "provavelmente" not in prompt and "OAB/PR" not in prompt
        assert "professional_registration" not in prompt and "representatives" not in prompt
        assert "EXTERNAL SUMMARY MUST BE IGNORED" not in prompt
        for party in item["response_data"]["parties"]:
            if party.get("main_document"):
                assert party["main_document"] not in prompt
        steps = json.loads(prompt.split("<movimentos_json>\n")[1].split("\n</movimentos_json>")[0])
        assert len(steps) == 13
        assert [s["step_number"] for s in steps] == list(range(1, 14))
        assert [s["source_step_number"] for s in steps] == list(range(1, 14))
        assert "DISTRIBUÍDO" in steps[0]["text"] and "REMETIDOS" in steps[-1]["text"]
        hearing = next(s for s in steps if "scheduled_hearing" in s["source_temporal_facts"])
        assert hearing["source_temporal_facts"]["scheduled_hearing"] == {"date": "03/03/2027", "time": "15:00"}
        process_json = json.loads(prompt.split("<processo_json>\n")[1].split("\n</processo_json>")[0])
        knowledge = process_json["legal_knowledge"]
        assert any(item["id"] == "classe-436" and item["match_method"] == "code" for item in knowledge)
        assert any(item["id"] == "assunto-14033" and item["match_method"] == "code" for item in knowledge)
        assert any(item["id"] == "mov-intimacao" for item in knowledge)
        assert len({item["id"] for item in knowledge}) == len(knowledge)
        payload = _writing_payload(process_json, steps)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))], stop_reason="end_turn", usage=None)
    async def close():
        pass
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create), close=close))
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE tenants, processes, jobs, judit_deliveries, access_log CASCADE")
            await conn.execute("INSERT INTO tenants(id,name) VALUES($1,'Synthetic parser tenant'),($2,'Other tenant')", tenant, other)
            await conn.execute("INSERT INTO tenant_judit_requests(tenant_id,process_code,judit_request_id) VALUES($1,$2,$3)", tenant, code, request_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/webhooks/judit/m01-synthetic-webhook", json=page)).status_code == 200
            completion = {"event_type": "request_completed", "callback_id": f"complete-{request_id}", "reference_type": "request", "reference_id": request_id, "payload": {"status": "completed"}}
            assert (await client.post("/webhooks/judit/m01-synthetic-webhook", json=completion)).status_code == 200
            worker = Worker(pool, WorkerSettings(database_url=URL, concurrency=1, heartbeat_interval_seconds=60,
                stale_after_seconds=120, task_timeout_seconds=20, reclaim_interval_seconds=60))
            assert await worker.process_one()
            async with pool.acquire() as conn:
                process = await conn.fetchrow("SELECT * FROM processes WHERE code=$1", code)
                rows = await conn.fetch("SELECT id,step_number,metadata FROM process_steps WHERE process_id=$1 ORDER BY step_number", process["id"])
                assert [r["metadata"]["source_step_number"] for r in rows] == list(range(1, 14))
                assert [r["metadata"]["source_input_index"] for r in rows] == list(range(13, 0, -1))
                assert [r["metadata"]["step_id"] for r in rows] == [f"m01-step-{i:02d}" for i in range(13, 0, -1)]
                assert len(process["header"]["source_parser"]["indicators"]) == 3
                assert process["class_name"] == "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL"
                assert process["subjects"][0]["code"] == "14033"
                assert all(i["kind"] == "heuristic" for i in process["header"]["source_parser"]["indicators"])
                configure_approval(monkeypatch, tmp_path, (process["id"], process["current_version_id"], tenant), approval_id="APR-M01-" + uuid4().hex.upper())
                monkeypatch.setenv("DATABASE_URL", URL)
                before = [(r["id"], r["metadata"]) for r in rows]
            assert await worker.process_one()
            assert not await worker.process_one()
            response = await client.get(f"/processes/{code}", headers={"Authorization": "Bearer m01-owner"})
            assert response.status_code == 200
            body = response.json()
            assert body["summary"] is not None, await pool.fetchval("SELECT validation FROM process_summaries WHERE process_id=$1", process["id"])
            assert body["summary"]["validation"]["passed"] is True
            assert body["summary"]["model"] == PROPOSED_MODEL
            assert body["iaSummary"] == body["summary"]["markdown"]
            # Compare the *published* document on this exact commit to the
            # pinned donor oracle. The archived M03 markdown is not an
            # acceptable substitute for a runtime parity regression gate.
            runtime_parity = evaluate_writing_parity(destination_text=body["iaSummary"])
            assert runtime_parity["synthetic"] is True
            assert runtime_parity["destination_origin"] == "runtime_api"
            assert runtime_parity["passed"], [
                (item["requirement"], item["status"])
                for item in runtime_parity["results"]
                if item["status"] not in {"COVERED", "DIVERGENCE_JUSTIFIED"}
            ]
            assert runtime_parity["destination_document_sha256"] == __import__(
                "hashlib"
            ).sha256(body["iaSummary"].encode("utf-8")).hexdigest()
            assert "Contexto do processo:" in body["iaSummary"]
            assert "Andamento processual:" in body["iaSummary"]
            assert "3º O Juizado Especial Cível tem competência" in body["iaSummary"]
            assert "VI - a efetiva prevenção e reparação" in body["iaSummary"]
            assert "Intimação é o ato pelo qual" in body["iaSummary"]
            assert "01/09/2026" in body["iaSummary"] and "03/03/2027 15:00" in body["iaSummary"]
            assert "polo cadastrado: ativo" in body["iaSummary"]
            assert "Pessoa Sintética Beta (representa: Pessoa Sintética Alfa e Pessoa Sintética Gama)" in body["iaSummary"]
            assert "envio do processo de um setor" not in body["iaSummary"]
            for forbidden in ("source_parser", "Ignore a área", "provavelmente", "OAB/PR"):
                assert forbidden not in body["iaSummary"]
            # One acquired/versioned Judit process, one persisted summary:
            # verify both public representations and source provenance on the
            # same canonical output, not on a separately authored fixture.
            owner_headers = {"Authorization": "Bearer m01-owner"}
            summary_jsx_response = await client.get(
                f"/v1/processos/{code}/resumo", headers=owner_headers
            )
            assert summary_jsx_response.status_code == 200
            summary_jsx = summary_jsx_response.json()
            assert summary_jsx["format"] == "jsx"
            assert summary_jsx["validation"]["passed"] is True
            assert summary_jsx["iaSummary"] == body["iaSummary"]
            assert summary_jsx["cnj"] == code
            assert summary_jsx["claim_evidence"]
            assert all(
                claim["verification_status"] == "supported"
                for claim in summary_jsx["claim_evidence"]
            )

            summary_json_response = await client.get(
                f"/v1/processos/{code}/resumo?format=json",
                headers=owner_headers,
            )
            assert summary_json_response.status_code == 200
            summary_json = summary_json_response.json()
            assert summary_json["format"] == "json"
            document = summary_json["iaSummary"]
            assert document["process"]["cnj"] == code
            assert document["summary"]["claims"]
            assert "Contexto do processo:" in document["summary"]["synthesis"]
            assert len(document["summary"]["timeline"]) == 13
            assert all(
                claim["verification"]["status"] == "supported"
                for claim in document["summary"]["claims"]
            )

            source_response = await client.get(
                f"/v1/processos/{code}/fontes", headers=owner_headers
            )
            assert source_response.status_code == 200
            source_payload = source_response.json()
            assert source_payload["cnj"] == code
            assert source_payload["claim_evidence"]
            assert any(
                source["kind"] == "movement" and source["used_for_summary"]
                for source in source_payload["sources"]
            )
            assert not any(
                "evidence_excerpt" in source
                for claim in source_payload["claim_evidence"]
                for source in claim["sources"]
            )
            for endpoint in (
                f"/v1/processos/{code}/resumo",
                f"/v1/processos/{code}/resumo?format=json",
                f"/v1/processos/{code}/fontes",
            ):
                forbidden = await client.get(
                    endpoint, headers={"Authorization": "Bearer m01-other"}
                )
                assert forbidden.status_code in (403, 404)

            assert (await client.get(f"/processes/{code}", headers={"Authorization": "Bearer m01-other"})).status_code in (403, 404)
            assert (await client.post("/webhooks/judit/m01-synthetic-webhook", json=page)).status_code == 200
            async with pool.acquire() as conn:
                after = await conn.fetch("SELECT id,metadata FROM process_steps WHERE process_id=$1 ORDER BY step_number", process["id"])
                assert [(r["id"], r["metadata"]) for r in after] == before
                assert await conn.fetchval("SELECT count(*) FROM process_summary_sources") == 13
                knowledge_sources = await conn.fetch("SELECT kind,code,tpu_version,source_ref,definition_sha256 FROM process_summary_glossary_sources ORDER BY source_order")
                assert {row["kind"] for row in knowledge_sources} >= {"class", "subject", "movement", "concept"}
                assert all(len(row["definition_sha256"]) == 64 for row in knowledge_sources)
                assert all(row["source_ref"].startswith("https://") for row in knowledge_sources)
                assert {row["tpu_version"] for row in knowledge_sources if row["kind"] in {"class", "subject"}} == {"ragvia-legal-m02-v1"}
                claims = await conn.fetch("SELECT claim_id,verification_status FROM process_summary_claims WHERE process_id=$1", process["id"])
                synthesis = next(row for row in claims if row["claim_id"] == "synthesis")
                assert synthesis["verification_status"] == "supported"
                citation_kinds = await conn.fetch("SELECT DISTINCT s.source_kind FROM process_summary_claims c JOIN process_summary_claim_sources s ON s.claim_row_id=c.id WHERE c.process_id=$1 AND c.claim_id='synthesis'", process["id"])
                assert {row["source_kind"] for row in citation_kinds} >= {"process", "movement"}
                excerpt = await conn.fetchval("SELECT evidence_excerpt FROM process_summary_claim_evidence_excerpts e JOIN process_summary_claims c ON c.id=e.claim_row_id WHERE c.process_id=$1 AND c.claim_id='synthesis'", process["id"])
                assert excerpt and "Intimação é o ato pelo qual" in body["iaSummary"]
        assert len(outbound) == 1

        # Exercise the canonical pgvector fallback with synthetic local BGE vectors only.
        import app.embedding_runtime as embedding_runtime
        from app.rag import _retrieve_supplemental_knowledge
        entry = next(value for value in load_legal_knowledge() if value.id == "mov-citacao")
        vector = [1.0] + [0.0] * 1023
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO legal_knowledge_embeddings(entry_id,embedding_content_sha256,provider,model,dimensions,embedding) VALUES($1,$2,'bge','BAAI/bge-m3',1024,$3::vector)", entry.id, entry.embedding_sha256, vector)
        class SyntheticLocalBGE:
            space = SimpleNamespace(provider="bge", model="BAAI/bge-m3", dimensions=1024)
            async def embed_query(self, query):
                return [1.0] + [0.0] * 1023
        monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "true")
        monkeypatch.setattr(embedding_runtime, "get_active_embedding_runtime", lambda: SyntheticLocalBGE())
        semantic, _ = await _retrieve_supplemental_knowledge(
            pool, context={"header": {}, "class_name": "termo sem correspondência", "subjects": []},
            steps=[{"text": "evento não indexado lexicalmente"}],
        )
        assert any(item["id"] == entry.id and item["match_method"] == "semantic_bge" for item in semantic)
        class OrthogonalBGE(SyntheticLocalBGE):
            async def embed_query(self, query):
                return [0.0, 1.0] + [0.0] * 1022
        monkeypatch.setattr(embedding_runtime, "get_active_embedding_runtime", lambda: OrthogonalBGE())
        below_threshold, _ = await _retrieve_supplemental_knowledge(
            pool, context={"header": {}, "class_name": "termo não relacionado", "subjects": []},
            steps=[{"text": "evento não relacionado"}],
        )
        assert not any(item["id"] == entry.id for item in below_threshold)
        await pool.execute("DELETE FROM legal_knowledge_embeddings WHERE entry_id=$1", entry.id)
        monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "false")

        if directory := os.getenv("RAGV_M01_ARTIFACT_DIR"):
            out = Path(directory)
            out.mkdir(parents=True, exist_ok=True)
            (out / "summary.md").write_text(body["iaSummary"], encoding="utf-8")
            (out / "api-response.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            (out / "browser-auth.json").write_text(json.dumps({"tenant_id": str(tenant), "token": "m01-owner", "code": code}, indent=2), encoding="utf-8")
            (out / "evidence.json").write_text(json.dumps({"synthetic": True, "provider_calls": 0, "provider_cost_usd": "0", "stub_messages": len(outbound), "movement_sources": 13, "legal_knowledge_sources": len(knowledge_sources)}, indent=2), encoding="utf-8")
    finally:
        await pool.close()
