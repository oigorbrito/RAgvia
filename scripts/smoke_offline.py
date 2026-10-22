"""Single-process offline release proof over the real PostgreSQL-backed app."""
from __future__ import annotations
import asyncio, os
from pathlib import Path
from uuid import uuid4
import httpx
import app.rag as rag
import app.process_requests as process_requests
from app.claim_evidence import build_material_claims
from app.summary_output import structured_summary_document
from app.api import app
from app.db import create_pool
from app.migrations import migrate
from app.worker import Worker, WorkerSettings

async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    webhook_token, tenant_id, bearer, other_bearer = "offline-smoke-webhook", uuid4(), "offline-smoke-bearer", "offline-smoke-other"
    request_id, response_id, callback_id = (f"offline-{uuid4()}" for _ in range(3))
    code = "0000000-00.2026.8.21.0001"
    await migrate(database_url, migrations_dir=Path("/app/sql"))
    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE jobs, judit_deliveries, judit_request_completions, tenant_judit_requests, process_summaries, process_steps, tenant_processes, access_log, process_versions, processes, tenants RESTART IDENTITY CASCADE")
            await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'offline smoke tenant')", tenant_id)
        app.state.pool, app.state.webhook_tenant_id = pool, tenant_id
        app.state.bearer_tokens = {bearer: tenant_id, other_bearer: uuid4()}
        os.environ["JUDIT_WEBHOOK_TOKEN"] = webhook_token
        def forbidden(*_args, **_kwargs): raise AssertionError("offline smoke invoked a paid provider")
        async def fake_generate(_client, _context, validation_errors=None):
            assert validation_errors is None
            payload = {
                "synthesis": f"Processo {code}. Situação processual registrada.",
                "timeline": [],
                "current_status": "Situação processual registrada.",
                "attention": ["Nenhuma divergência objetiva identificada."],
                "decisions": [],
                "deadlines": [],
                "related_processes": [],
                "attachments": [],
            }
            payload["claims"] = build_material_claims(
                payload,
                evidence_refs=[str(_context["_process_evidence_ref"])],
            )
            _context["_parsed_summary"] = payload
            _context["_structured_summary"] = structured_summary_document(
                payload, _context
            )
            return f"# Resumo do processo\n\nProcesso {code}. Situação processual registrada.\n\n## Pontos de atenção\nNenhuma divergência objetiva identificada."
        rag.anthropic_client = lambda *_args, **_kwargs: object()
        rag._generate = fake_generate
        process_requests.create_lawsuit_request = forbidden
        rag.ensure_step_embeddings = forbidden
        rag.embed_query = forbidden
        lawsuit = {"callback_id": callback_id, "event_type": "response_created", "reference_type": "request", "reference_id": request_id, "payload": {"request_id": request_id, "response_id": response_id, "response_type": "lawsuit", "response_data": {"code": code, "class_name": "Ação Cível", "court": "TJRS", "header": {}, "parties": [{"name": "Parte A"}], "subjects": [{"name": "Contrato"}], "steps": [{"step_number": i, "title": f"Movimento {i}", "text": "Registro sintético."} for i in range(1, 4)]}, "tags": {"cached_response": False}}}
        completion = {"callback_id": f"offline-completion-{uuid4()}", "event_type": "request_completed", "reference_type": "request", "reference_id": request_id, "payload": {"status": "completed"}}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://offline") as client:
            assert (await client.post(f"/webhooks/judit/{webhook_token}", json=lawsuit)).status_code == 200
            assert (await client.post(f"/webhooks/judit/{webhook_token}", json=completion)).status_code == 200
        worker = Worker(pool, WorkerSettings(database_url=database_url, concurrency=1, heartbeat_interval_seconds=60, stale_after_seconds=120, task_timeout_seconds=20, reclaim_interval_seconds=60))
        while await worker.process_one(): pass
        async with pool.acquire() as conn:
            state = await conn.fetchrow("SELECT p.current_version_id, count(DISTINCT p.id) AS processes, count(DISTINCT pv.id) AS versions, count(DISTINCT ps.id) FILTER (WHERE (ps.validation->>'passed')::boolean) AS valid_summaries, count(DISTINCT ps.id) AS summaries FROM processes p JOIN process_versions pv ON pv.id = p.current_version_id LEFT JOIN process_summaries ps ON ps.version_id = pv.id WHERE p.code = $1 GROUP BY p.current_version_id", code)
            jobs = await conn.fetchrow("SELECT count(*) FILTER (WHERE status = 'completed') AS completed, count(*) FILTER (WHERE status = 'dead') AS dead FROM jobs")
            logical_jobs = await conn.fetchrow("SELECT count(*) AS total, count(DISTINCT idempotency_key) AS unique_keys FROM jobs WHERE idempotency_key LIKE $1", f"%{request_id}%")
            assert state and state["current_version_id"] and int(state["processes"]) == 1 and int(state["versions"]) == 1
            assert int(state["summaries"]) == 1 and int(state["valid_summaries"]) == 1 and int(state["current_version_id"] is not None)
            assert jobs["completed"] >= 2 and jobs["dead"] == 0 and logical_jobs["total"] == logical_jobs["unique_keys"]
            assert await conn.fetchval("SELECT count(*) FROM process_steps ps JOIN process_versions pv ON pv.id = ps.version_id WHERE pv.id = $1", state["current_version_id"]) == 3
        async with httpx.AsyncClient(transport=transport, base_url="http://offline") as client:
            response = await client.get(f"/processes/{code}", headers={"Authorization": f"Bearer {bearer}"})
            assert response.status_code == 200 and response.json()["summary_status"] == "available" and response.json()["summary"]["validation"]["passed"] is True
            unauthorized = await client.get(f"/processes/{code}", headers={"Authorization": f"Bearer {other_bearer}"})
            assert unauthorized.status_code == 404 and code not in unauthorized.text
        print(f"RPY OFFLINE SMOKE: PASS\nprocess={code}\nsummary=valid\njobs=complete\nproviders=0")
    finally: await pool.close()

if __name__ == "__main__": asyncio.run(main())
