"""Synthetic delivery acceptance: stub only the Anthropic boundary, not generation/validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import app.rag as rag
from app.api import app
from app.claim_evidence import build_material_claims
from app.db import create_pool
from app.migrations import migrate
from app.worker import Worker, WorkerSettings
from app import providers
from app.generation_authorization import PROPOSED_MODEL
from tests.test_generation_authorization import configure_approval

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/judit/ragvia_02a_synthetic.json"


async def test_synthetic_legal_document_uses_canonical_structured_generation(monkeypatch, tmp_path):
    assert TEST_DATABASE_URL
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert source["secrecy_level"] == 0
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    previous_pool = getattr(app.state, "pool", None)
    app.state.pool = pool
    tenant_id, other_tenant = uuid4(), uuid4()
    monkeypatch.setattr(app.state, "bearer_tokens", {"synthetic-owner": tenant_id, "synthetic-other": other_tenant}, raising=False)
    monkeypatch.setattr(app.state, "webhook_tenant_id", tenant_id, raising=False)
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "synthetic-02a-webhook")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-not-a-provider-credential")
    monkeypatch.setenv("ANTHROPIC_MODEL", PROPOSED_MODEL)
    monkeypatch.setenv("PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DATAJUD_ENABLED", "false")
    calls = []

    async def create(**request):
        calls.append(request)
        assert request["model"] == PROPOSED_MODEL
        assert request["max_tokens"] == 4000
        assert "tools" not in request
        assert request["output_config"]["format"]["schema"]["additionalProperties"] is False
        prompt = request["messages"][0]["content"]
        process = json.loads(prompt.split("<processo_json>\n")[1].split("\n</processo_json>")[0])
        steps = json.loads(prompt.split("<movimentos_json>\n")[1].split("\n</movimentos_json>")[0])
        assert len(steps) == 4  # short-process bypass: no paid embeddings/retrieval
        assert [s["occurred_at"][:10] for s in steps] == ["2026-01-10", "2026-01-20", "2026-02-10", "2026-04-10"]
        assert "synthetic-not-a-provider-credential" not in prompt
        payload = {
            "synthesis": "Demonstração sintética de ação de cobrança, com distribuição em 10/01/2026 e sentença em 10/04/2026.",
            "timeline": [step["text"] for step in steps],
            "current_status": steps[-1]["text"],
            "attention": [
                "Documento exclusivamente sintético; não corresponde ao caso real do gerente.",
                "O recorte não fornece anexos, prazo recursal ou informação de trânsito em julgado; não se presume encerramento definitivo.",
            ],
            "decisions": [steps[-1]["text"]],
            "deadlines": [], "related_processes": [], "attachments": [],
        }
        refs = [process["evidence_ref"], *(step["evidence_ref"] for step in steps)]
        claims = build_material_claims(payload, evidence_refs=refs)
        # Specific factual chronology links to the exact source movement, not a generic reference.
        for index, step in enumerate(steps):
            next(c for c in claims if c["claim_id"] == f"timeline:{index}")["evidence_refs"] = [step["evidence_ref"]]
        next(c for c in claims if c["claim_id"] == "decisions:0")["evidence_refs"] = [steps[-1]["evidence_ref"]]
        payload["claims"] = claims
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
            stop_reason="end_turn", usage=None,  # do not invent usage/cost for a fake provider
        )

    async def close():
        pass
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create), close=close))
    async def forbidden_external(*args, **kwargs):
        raise AssertionError("short synthetic acceptance must not call external embeddings")
    monkeypatch.setattr(rag, "ensure_step_embeddings", forbidden_external)
    request_id = f"synthetic-02a-{uuid4()}"
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE tenants, processes, jobs, judit_deliveries, access_log CASCADE")
            await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'Synthetic acceptance'), ($2, 'Other tenant')", tenant_id, other_tenant)
            # Offline import still needs durable request/tenant correlation; no paid acquisition.
            await conn.execute("INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id) VALUES ($1,$2,$3)", tenant_id, source["code"], request_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for event, payload in (
                ("response_created", {"request_id": request_id, "response_id": f"response-{request_id}",
                                      "response_type": "lawsuit", "response_data": source,
                                      "tags": {"cached_response": False}}),
                ("request_completed", {"status": "completed"}),
            ):
                response = await client.post("/webhooks/judit/synthetic-02a-webhook", json={
                    "callback_id": f"{event}-{request_id}", "event_type": event,
                    "reference_type": "request", "reference_id": request_id, "payload": payload,
                })
                assert response.status_code == 200
            worker = Worker(pool, WorkerSettings(database_url=TEST_DATABASE_URL, concurrency=1,
                heartbeat_interval_seconds=60, stale_after_seconds=120,
                task_timeout_seconds=20, reclaim_interval_seconds=60))
            assert await worker.process_one() is True
            async with pool.acquire() as conn:
                process = await conn.fetchrow("SELECT id, current_version_id FROM processes WHERE code=$1", source["code"])
                configure_approval(monkeypatch, tmp_path, (process["id"], process["current_version_id"], tenant_id))
                monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
                pending = await conn.fetch("SELECT id, task_name FROM jobs WHERE status='pending'")
                assert len(pending) == 1 and pending[0]["task_name"] == "generate_process_summary"
                await conn.execute("UPDATE jobs SET max_attempts=1 WHERE id=$1 AND status='pending'", pending[0]["id"])
            assert await worker.process_one() is True
            assert await worker.process_one() is False
            assert len(calls) == 1
            url = f"/processes/{source['code']}"
            denied = await client.get(url, headers={"Authorization": "Bearer synthetic-other"})
            assert denied.status_code in (403, 404)
            response = await client.get(url, headers={"Authorization": "Bearer synthetic-owner"})
            assert response.status_code == 200
            body = response.json()
        if os.getenv("RAGV_02B_LIVE_BROWSER") == "true":
            from tests.live_legal_browser import verify_live_browser
            await verify_live_browser(app, code=source["code"], token="synthetic-owner")
        assert body["summary"]["validation"]["passed"] is True
        assert body["summary"]["model"] == PROPOSED_MODEL
        assert isinstance(body["iaSummary"], str)
        assert body["iaSummary"] == body["summary"]["markdown"]
        assert "10/04/2026" in body["iaSummary"]
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT markdown, structured_output, model FROM process_summaries WHERE process_id=(SELECT id FROM processes WHERE code=$1)", source["code"])
            assert row["markdown"] == body["iaSummary"]
            structured = row["structured_output"]
            if isinstance(structured, str):
                structured = json.loads(structured)
            assert structured["schema_version"] == 3
            assert len(structured["summary"]["claims"]) >= 7
            assert await conn.fetchval("SELECT count(*) FROM process_summary_claims") >= 7
        if output := os.getenv("RAGV_02A_ARTIFACT_DIR"):
            destination = Path(output)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "api-response.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            (destination / "summary.md").write_text(body["iaSummary"], encoding="utf-8")
            (destination / "structured-summary.json").write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8")
            (destination / "evidence.json").write_text(json.dumps({
                "synthetic": True, "paid_provider_calls": 0, "provider_cost_usd": "0", "stubbed_messages": len(calls),
                "authorization_kind": "synthetic-fixture-not-account-evidence",
                "browser_transport": "live_http" if os.getenv("RAGV_02B_LIVE_BROWSER") == "true" else "not_run",
                "model": body["summary"]["model"], "validation": body["summary"]["validation"],
                "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                "markdown_sha256": hashlib.sha256(body["iaSummary"].encode()).hexdigest(),
                "usage": None, "cost_usd": None,
            }, indent=2), encoding="utf-8")
    finally:
        app.state.pool = previous_pool
        await pool.close()
