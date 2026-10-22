from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app.datajud_client import DataJudLookupResult
from app.datajud_enrichment import DataJudMetadata
from app.datajud_jobs import DATAJUD_TASK
from app.db import create_pool
from app.migrations import migrate
from app.queue import enqueue
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL must name a disposable PostgreSQL database")
CNJ = "0000000-00.2024.8.26.0100"


async def _setup(monkeypatch):
    assert TEST_DATABASE_URL  # nosec B101
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE jobs, tenants, processes RESTART IDENTITY CASCADE")
    tenants = [uuid4(), uuid4()]
    tokens = [f"datajud-{uuid4()}", f"datajud-{uuid4()}"]
    process_id, version_id = uuid4(), uuid4()
    source_payload = {
        "event_type": "response_created",
        "callback_id": "datajud-test-callback",
        "payload": {
            "request_id": "datajud-test-request",
            "response_id": "datajud-test-response",
            "response_type": "lawsuit",
            "response_data": {"code": CNJ, "secrecy_level": 0},
        },
    }
    async with pool.acquire() as conn:
        for tenant_id in tenants:
            await conn.execute("INSERT INTO tenants(id,name) VALUES($1,$2)", tenant_id, "DataJud tenant")
        await conn.execute(
            "INSERT INTO processes(id,code,secrecy_level) VALUES($1,$2,0)",
            process_id,
            CNJ,
        )
        await conn.execute(
            """INSERT INTO process_versions(
                   id,process_id,source_request_id,source_payload,finalized,finalized_at
               ) VALUES($1,$2,'datajud-test-request',$3::jsonb,TRUE,NOW())""",
            version_id,
            process_id,
            json.dumps(source_payload),
        )
        await conn.execute(
            "UPDATE processes SET current_version_id=$1 WHERE id=$2",
            version_id,
            process_id,
        )
        await conn.execute(
            "INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)",
            tenants[0],
            process_id,
        )
    monkeypatch.setattr(app.state, "bearer_tokens", dict(zip(tokens, tenants)), raising=False)
    monkeypatch.setattr(app.state, "pool", pool, raising=False)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    return pool, tenants, tokens


async def _submit(client, path, headers, code=CNJ):
    return await client.post(path, headers=headers, json={"code": code})


async def _status(client, path, headers, code, job_id):
    return await client.post(path, headers=headers, json={"code": code, "job_id": str(job_id)})


async def _set_process_secrecy(pool, *, stored_level: int, source_level: int | None):
    async with pool.acquire() as conn:
        process_id = await conn.fetchval(
            "SELECT id FROM processes WHERE code=$1", CNJ
        )
        version_id = uuid4()
        source_request_id = version_id.hex
        response_data = {"code": CNJ}
        if source_level is not None:
            response_data["secrecy_level"] = source_level
        source_payload = {
            "event_type": "response_created",
            "callback_id": f"datajud-test-{version_id}",
            "payload": {
                "request_id": source_request_id,
                "response_id": source_request_id,
                "response_type": "lawsuit",
                "response_data": response_data,
            },
        }
        await conn.execute(
            """INSERT INTO process_versions(
                   id,process_id,source_request_id,source_payload,finalized,finalized_at
               ) VALUES($1,$2,$3,$4::jsonb,TRUE,NOW())""",
            version_id,
            process_id,
            source_request_id,
            json.dumps(source_payload),
        )
        await conn.execute(
            "UPDATE processes SET secrecy_level=$1,current_version_id=$2 WHERE id=$3",
            stored_level,
            version_id,
            process_id,
        )


def _worker(pool):
    return Worker(
        pool,
        WorkerSettings(
            database_url=TEST_DATABASE_URL,
            concurrency=1,
            heartbeat_interval_seconds=60,
            stale_after_seconds=120,
            task_timeout_seconds=30,
            reclaim_interval_seconds=60,
        ),
    )


@pytest.mark.asyncio
async def test_versioned_aliases_use_the_same_durable_job_contract(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for index, path in enumerate(("/processos/datajud/jobs", "/v1/processos/datajud/jobs")):
                headers = {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": f"alias-{index}"}
                submitted = await _submit(client, path, headers)
                assert submitted.status_code == 202  # nosec B101
                status_path = f"{path}/status"
                status = await _status(client, status_path, {"Authorization": f"Bearer {tokens[0]}"}, CNJ, submitted.json()["job_id"])
                assert status.status_code == 202  # nosec B101
                assert status.json()["job_id"] == submitted.json()["job_id"]  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_datajud_job_persists_result_is_idempotent_and_isolated(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    calls = []

    async def fake_lookup(*, code, secrecy_level, config):
        calls.append((code, secrecy_level))
        assert secrecy_level == 0  # nosec B101
        assert config.api_key == "worker-only-test-key"  # nosec B101
        return DataJudLookupResult(
            status="ok",
            metadata=DataJudMetadata(
                class_name="Procedimento de teste",
                class_code="123",
                subjects=({"code": "456", "name": "Assunto de teste"},),
                adjudicating_body="Vara de teste",
                county="Comarca de teste",
            ),
        )

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", fake_lookup)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_headers = {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "request-1"}
            first = await _submit(client, "/processes/datajud/jobs", first_headers)
            replay = await _submit(client, "/processes/datajud/jobs", first_headers)
            assert first.status_code == replay.status_code == 202  # nosec B101
            assert first.json()["created"] is True and replay.json()["created"] is False  # nosec B101
            assert first.json()["job_id"] == replay.json()["job_id"]  # nosec B101
            assert "DATAJUD_API_KEY" not in os.environ  # nosec B101

            conflict = await _submit(
                client,
                "/processes/datajud/jobs",
                first_headers,
                code="0000000-00.2024.8.26.0101",
            )
            assert conflict.status_code == 409  # nosec B101

            other = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[1]}", "Idempotency-Key": "request-1"},
            )
            assert other.status_code == 202  # nosec B101
            assert other.json()["created"] is True  # nosec B101

            unknown = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": "Bearer invalid", "Idempotency-Key": "request-2"},
            )
            assert unknown.status_code == 401  # nosec B101
            cross_tenant = await _status(
                client,
                "/processes/datajud/jobs/status",
                {"Authorization": f"Bearer {tokens[1]}"},
                CNJ,
                first.json()["job_id"],
            )
            assert cross_tenant.status_code == 404  # nosec B101
            pending = await _status(
                client,
                "/processes/datajud/jobs/status",
                {"Authorization": f"Bearer {tokens[0]}"},
                CNJ,
                first.json()["job_id"],
            )
            assert pending.status_code == 202  # nosec B101

            monkeypatch.setenv("DATAJUD_API_KEY", "worker-only-test-key")
            assert await _worker(pool).process_one() is True  # nosec B101
            completed = await _status(
                client,
                "/processes/datajud/jobs/status",
                {"Authorization": f"Bearer {tokens[0]}"},
                CNJ,
                first.json()["job_id"],
            )
            assert completed.status_code == 200  # nosec B101
            assert completed.json() == {  # nosec B101
                "code": CNJ,
                "source": "datajud",
                "court": "TJSP",
                "class_name": "Procedimento de teste",
                "adjudicating_body": "Vara de teste",
                "county": "Comarca de teste",
                "subjects": [{"code": "456", "name": "Assunto de teste"}],
                "parties": [],
            }
            assert calls == [(CNJ, 0)]  # nosec B101
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT status::text AS status, result FROM jobs WHERE id=$1", first.json()["job_id"])
            assert row and row["status"] == "completed" and row["result"]["status"] == "ok"  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_new_cnj_without_any_process_row_flows_api_queue_worker_and_history(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    code = "0000000-00.2024.8.26.0103"
    calls = []

    async def fake_lookup(**kwargs):
        calls.append(kwargs["secrecy_level"])
        return DataJudLookupResult(status="ok", metadata=DataJudMetadata(
            class_name="Metadado simulado", class_code="91", subjects=(),
            adjudicating_body="Unidade simulada", county=None,
        ))

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", fake_lookup)
    monkeypatch.setenv("DATAJUD_API_KEY", "worker-only-test-key")
    transport = httpx.ASGITransport(app=app)
    try:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM processes WHERE code=$1", code) == 0  # nosec B101
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "new-cnj"}
            first = await client.post("/processes/datajud/jobs", headers=headers, json={"code": code})
            replay = await client.post("/processes/datajud/jobs", headers=headers, json={"code": code})
            assert first.status_code == replay.status_code == 202  # nosec B101
            assert first.json()["job_id"] == replay.json()["job_id"]  # nosec B101
            assert first.json()["created"] is True and replay.json()["created"] is False  # nosec B101
            pending = await _status(client, "/processes/datajud/jobs/status", {"Authorization": f"Bearer {tokens[0]}"}, code, first.json()["job_id"])
            assert pending.status_code == 202  # nosec B101
            assert await _worker(pool).process_one() is True  # nosec B101
            completed = await _status(client, "/processes/datajud/jobs/status", {"Authorization": f"Bearer {tokens[0]}"}, code, first.json()["job_id"])
            assert completed.status_code == 200  # nosec B101
            assert completed.json()["class_name"] == "Metadado simulado"  # nosec B101
            history = await client.get("/processes/datajud/history", headers={"Authorization": f"Bearer {tokens[0]}"})
            assert history.status_code == 200  # nosec B101
            assert any(item["job_id"] == first.json()["job_id"] and item["metadata"]["class_name"] == "Metadado simulado" for item in history.json()["items"])  # nosec B101
            cross_tenant = await _status(client, "/processes/datajud/jobs/status", {"Authorization": f"Bearer {tokens[1]}"}, code, first.json()["job_id"])
            assert cross_tenant.status_code == 404  # nosec B101
        async with pool.acquire() as conn:
            durable = await conn.fetchrow("SELECT status::text AS status, payload, result FROM jobs WHERE id=$1", first.json()["job_id"])
        assert durable["status"] == "completed" and durable["result"]["status"] == "ok"  # nosec B101
        assert durable["payload"]["purpose"] == "public_metadata_lookup"  # nosec B101
        assert durable["payload"]["operation"] == "metadata_lookup"  # nosec B101
        assert durable["payload"]["classification_at_request"] == "unknown"  # nosec B101
        assert calls == [None]  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_default_zero_without_judit_source_is_unknown_but_independent_lookup_is_allowed(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    code_without_source = "0000000-00.2024.8.26.0102"
    process_id = uuid4()
    calls = []

    async def fake_lookup(**kwargs):
        calls.append(kwargs["secrecy_level"])
        return DataJudLookupResult(status="ok", metadata=DataJudMetadata(class_name="Classe pública", class_code=None, subjects=(), adjudicating_body=None, county=None))

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", fake_lookup)
    monkeypatch.setenv("DATAJUD_API_KEY", "worker-only-test-key")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO processes(id,code) VALUES($1,$2)",
            process_id,
            code_without_source,
        )
        await conn.execute(
            "INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)",
            tenants[0],
            process_id,
        )

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "no-source"},
                code=code_without_source,
            )
            assert response.status_code == 202  # nosec B101
            queued_job_id = response.json()["job_id"]
            async with pool.acquire() as conn:
                queued_payload = await conn.fetchval("SELECT payload FROM jobs WHERE id=$1", queued_job_id)
            assert queued_payload["classification_at_request"] == "unknown"  # nosec B101
            assert queued_payload["purpose"] == "public_metadata_lookup"  # nosec B101

        assert await _worker(pool).process_one() is True  # nosec B101
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status::text AS status, result FROM jobs WHERE id=$1", queued_job_id)
        assert row["status"] == "completed" and row["result"]["status"] == "ok"  # nosec B101
        assert calls == [None]  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_secret_is_blocked_but_unknown_classification_uses_independent_path(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    calls = []

    async def fake_lookup(**kwargs):
        calls.append(kwargs["secrecy_level"])
        return DataJudLookupResult(status="ok", metadata=DataJudMetadata(class_name=None, class_code=None, subjects=(), adjudicating_body=None, county=None))

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", fake_lookup)
    monkeypatch.setenv("DATAJUD_API_KEY", "worker-only-test-key")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "secret-process"}
            await _set_process_secrecy(pool, stored_level=1, source_level=1)
            secret = await _submit(client, "/processes/datajud/jobs", headers)
            assert secret.status_code == 403  # nosec B101

            unknown_code = "0000000-00.2024.8.26.0104"
            async with pool.acquire() as conn:
                process_id = uuid4()
                await conn.execute("INSERT INTO processes(id,code) VALUES($1,$2)", process_id, unknown_code)
                await conn.execute("INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)", tenants[0], process_id)
            unknown = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "unknown-process"},
                code=unknown_code,
            )
            assert unknown.status_code == 202  # nosec B101
            assert await _worker(pool).process_one() is True  # nosec B101
        async with pool.acquire() as conn:
            jobs = await conn.fetch("SELECT status::text AS status FROM jobs")
        assert [row["status"] for row in jobs] == ["completed"]  # nosec B101
        assert calls == [None]  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_worker_rechecks_secrecy_and_tenant_authorization_before_provider(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    calls = 0

    async def fake_lookup(**kwargs):
        nonlocal calls
        calls += 1
        return DataJudLookupResult(status="ok", metadata=DataJudMetadata(class_name="Resultado", class_code=None, subjects=(), adjudicating_body=None, county=None))

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", fake_lookup)
    monkeypatch.setenv("DATAJUD_API_KEY", "worker-only-test-key")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            accepted = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "race-to-secret"},
            )
            assert accepted.status_code == 202  # nosec B101
            await _set_process_secrecy(pool, stored_level=1, source_level=1)
            assert await _worker(pool).process_one() is True  # nosec B101

            unauthorized = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": "Bearer invalid", "Idempotency-Key": "wrong-tenant"},
            )
            assert unauthorized.status_code == 401  # nosec B101

        unknown_code = "0000000-00.2024.8.26.0105"
        async with pool.acquire() as conn:
            forged = await enqueue(
                conn,
                task_name=DATAJUD_TASK,
                payload={"tenant_id": str(tenants[1]), "code": unknown_code,
                         "operation": "metadata_lookup", "purpose": "public_metadata_lookup"},
                idempotency_key="forged-wrong-tenant",
            )
        assert forged is not None  # nosec B101
        assert await _worker(pool).process_one() is True  # nosec B101
        async with pool.acquire() as conn:
            statuses = await conn.fetch(
                "SELECT status::text AS status FROM jobs ORDER BY created_at"
            )
        assert [row["status"] for row in statuses] == ["dead", "completed"]  # nosec B101
        assert calls == 1  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_datajud_disabled_rejects_without_enqueue_or_provider_call(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    calls = 0

    async def forbidden_lookup(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("disabled DataJud must not reach the provider")

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", forbidden_lookup)
    monkeypatch.setenv("DATAJUD_ENABLED", "false")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "disabled"},
            )
            assert response.status_code == 403  # nosec B101
            monkeypatch.setenv("DATAJUD_ENABLED", "true")
            monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "false")
            unauthorized = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "not-authorized"},
            )
            assert unauthorized.status_code == 403  # nosec B101
            monkeypatch.delenv("DATAJUD_AUTHORIZED_USE")
            missing_authorization = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "missing-purpose-opt-in"},
            )
            assert missing_authorization.status_code == 403  # nosec B101
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM jobs") == 0  # nosec B101
        assert calls == 0  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_missing_worker_credential_fails_closed_without_provider_call(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    calls = 0

    async def forbidden_lookup(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("provider adapter must not run without worker credentials")

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", forbidden_lookup)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            accepted = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "missing-key"},
            )
            assert accepted.status_code == 202  # nosec B101
            assert await _worker(pool).process_one() is True  # nosec B101
            response = await _status(
                client,
                "/processes/datajud/jobs/status",
                {"Authorization": f"Bearer {tokens[0]}"},
                CNJ,
                accepted.json()["job_id"],
            )
            assert response.status_code == 502  # nosec B101
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status::text FROM jobs WHERE id=$1", accepted.json()["job_id"])
        assert status == "dead" and calls == 0  # nosec B101
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_provider_no_hit_and_failure_are_sanitized(monkeypatch):
    pool, tenants, tokens = await _setup(monkeypatch)
    monkeypatch.setenv("DATAJUD_API_KEY", "worker-only-test-key")
    outcomes = iter((DataJudLookupResult(status="not_found"), DataJudLookupResult(status="unavailable", error_code="timeout")))

    async def fake_lookup(**kwargs):
        return next(outcomes)

    monkeypatch.setattr("app.datajud_tasks.lookup_datajud_metadata", fake_lookup)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "no-hit"}
            no_hit = await _submit(client, "/processes/datajud/jobs", headers)
            assert no_hit.status_code == 202  # nosec B101
            assert await _worker(pool).process_one() is True  # nosec B101
            not_found = await _status(
                client,
                "/processes/datajud/jobs/status",
                {"Authorization": f"Bearer {tokens[0]}"},
                CNJ,
                no_hit.json()["job_id"],
            )
            assert not_found.status_code == 404  # nosec B101

            failure = await _submit(
                client,
                "/processes/datajud/jobs",
                {"Authorization": f"Bearer {tokens[0]}", "Idempotency-Key": "provider-error"},
            )
            assert failure.status_code == 202  # nosec B101
            assert await _worker(pool).process_one() is True  # nosec B101
            unavailable = await _status(
                client,
                "/processes/datajud/jobs/status",
                {"Authorization": f"Bearer {tokens[0]}"},
                CNJ,
                failure.json()["job_id"],
            )
            assert unavailable.status_code == 502  # nosec B101
            assert "timeout" not in unavailable.text  # nosec B101

            history = await client.get(
                "/processes/datajud/history",
                headers={"Authorization": f"Bearer {tokens[0]}"},
            )
            assert history.status_code == 200  # nosec B101
            history_items = history.json()["items"]
            assert any(item["job_id"] == no_hit.json()["job_id"] and item["metadata"] is None for item in history_items)  # nosec B101
    finally:
        await pool.close()
