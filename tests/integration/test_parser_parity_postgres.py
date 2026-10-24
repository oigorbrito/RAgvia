"""Synthetic donor parser parity through canonical webhook/worker/structured publication."""
from __future__ import annotations

import json
import os
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
from app.worker import Worker, WorkerSettings
from tests.test_generation_authorization import configure_approval
from tests.test_parser_parity import source

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")


async def test_parser_annotations_survive_ingestion_but_never_cross_provider_boundary(monkeypatch, tmp_path):
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
        payload = {
            "synthesis": "Contexto do processo: demonstração sintética. Andamento processual: audiência de conciliação designada para 03/03/2027.",
            "timeline": [s["text"] for s in steps],
            "current_status": "Remessa interna registrada em 02/09/2026.",
            "attention": ["Demonstração sintética limitada ao recorte de movimentações fornecido."],
            "decisions": [], "deadlines": [], "related_processes": [], "attachments": [],
        }
        payload["claims"] = build_material_claims(payload, evidence_refs=[s["evidence_ref"] for s in steps])
        for i, step in enumerate(steps):
            next(c for c in payload["claims"] if c["claim_id"] == f"timeline:{i}")["evidence_refs"] = [step["evidence_ref"]]
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
            assert "03/03/2027" in body["iaSummary"]
            for forbidden in ("source_parser", "Ignore a área", "provavelmente", "OAB/PR"):
                assert forbidden not in body["iaSummary"]
            assert (await client.get(f"/processes/{code}", headers={"Authorization": "Bearer m01-other"})).status_code in (403, 404)
            assert (await client.post("/webhooks/judit/m01-synthetic-webhook", json=page)).status_code == 200
            async with pool.acquire() as conn:
                after = await conn.fetch("SELECT id,metadata FROM process_steps WHERE process_id=$1 ORDER BY step_number", process["id"])
                assert [(r["id"], r["metadata"]) for r in after] == before
                assert await conn.fetchval("SELECT count(*) FROM process_summary_sources") == 13
        assert len(outbound) == 1
        if directory := os.getenv("RAGV_M01_ARTIFACT_DIR"):
            out = Path(directory)
            out.mkdir(parents=True, exist_ok=True)
            (out / "summary.md").write_text(body["iaSummary"], encoding="utf-8")
            (out / "api-response.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            (out / "evidence.json").write_text(json.dumps({"synthetic": True, "provider_calls": 0, "provider_cost_usd": "0", "stub_messages": len(outbound), "movement_sources": 13}, indent=2), encoding="utf-8")
    finally:
        await pool.close()
