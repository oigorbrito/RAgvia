from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

import app.process_requests as process_requests
from app.api import app
from app.db import create_pool
from app.judit_client import JuditRequestError, JuditRequestResult
from app.migrations import migrate
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
CNJ = "0000000-00.2024.8.26.0100"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "judit" / "tracking_lawsuit_response.json"


async def _setup(monkeypatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, judit_request_completions,
                     process_summaries, process_steps, tenant_processes,
                     tenant_judit_requests, access_log, process_versions,
                     processes, tenants, acquisition_authorizations,
                     public_summary_requests, tenant_judit_request_history
            RESTART IDENTITY CASCADE
            """
        )
    tenant_id = uuid4()
    token = f"request-{uuid4()}"
    app.state.bearer_tokens = {token: tenant_id}
    app.state.pool = pool
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenants(id, name) VALUES($1, 'tenant')", tenant_id)
    return pool, tenant_id, token


def _worker(pool):
    assert TEST_DATABASE_URL is not None
    return Worker(
        pool,
        WorkerSettings(
            database_url=TEST_DATABASE_URL,
            concurrency=1,
            heartbeat_interval_seconds=60,
            stale_after_seconds=120,
            task_timeout_seconds=20,
            reclaim_interval_seconds=60,
        ),
    )


@pytest.mark.asyncio
async def test_1_first_authorized_acquisition(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fake_create(code):
        nonlocal calls
        calls += 1
        assert code == CNJ
        return JuditRequestResult(request_id="req-auth-1")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Pre-check confirms acquisition is required
        status_resp = await client.get(f"/processes/{CNJ}/status", headers=headers)
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["classification"] == "acquisition_required"
        assert status_data["history_available"] is False
        assert status_data["last_acquisition_at"] is None

        # 2. Issue authorization
        auth_resp = await client.post(
            f"/processes/{CNJ}/authorizations",
            headers=headers,
            json={"operation": "acquisition", "source": "judit"},
        )
        assert auth_resp.status_code == 201
        auth_token = auth_resp.json()["token"]

        # 3. Request acquisition using authorization token
        req_resp = await client.post(
            f"/processes/{CNJ}/request",
            headers={**headers, "X-Acquisition-Authorization": auth_token},
        )
        assert req_resp.status_code == 202
        assert req_resp.json()["created"] is True
        assert calls == 0

        # 4. Worker executes acquisition
        assert await _worker(pool).process_one() is True
        assert calls == 1

    async with pool.acquire() as conn:
        auth_consumed = await conn.fetchval(
            "SELECT consumed_at IS NOT NULL FROM acquisition_authorizations WHERE token = $1",
            auth_token,
        )
        assert auth_consumed is True
    await pool.close()


@pytest.mark.asyncio
async def test_2_cnj_with_available_history(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fake_create(code):
        nonlocal calls
        calls += 1
        return JuditRequestResult(request_id="req-hist-1")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)

    # Seed an existing process and completed request for this tenant
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        proc_id = await conn.fetchval("INSERT INTO processes (code) VALUES ($1) RETURNING id", CNJ)
        ver_id = await conn.fetchval(
            "INSERT INTO process_versions (process_id, source_request_id, finalized) VALUES ($1, 'req-hist-1', true) RETURNING id",
            proc_id,
        )
        await conn.execute("UPDATE processes SET current_version_id = $1 WHERE id = $2", ver_id, proc_id)
        await conn.execute("INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)", tenant_id, proc_id)
        await conn.execute(
            """
            INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id, status, completed_at)
            VALUES ($1, $2, 'req-hist-1', 'completed', $3)
            """,
            tenant_id,
            CNJ,
            now,
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Pre-check shows history available and read_existing
        status_resp = await client.get(f"/processes/{CNJ}/status", headers=headers)
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["classification"] == "read_existing"
        assert data["history_available"] is True
        assert data["source"] == "judit"
        assert data["last_acquisition_at"] is not None
        assert data["version_date"] is not None
        assert data["process_updated_at"] is not None
        assert data["acquired_today"] is True
        assert data["civil_date_sao_paulo"] is not None
        assert "consultado hoje" in data["advisory"]

        # 2. Precheck asking for refresh returns refresh_requires_confirmation
        refresh_check = await client.get(f"/processes/{CNJ}/status?operation=refresh", headers=headers)
        assert refresh_check.json()["classification"] == "refresh_requires_confirmation"

        # 3. Call without authorization: returns available, does NOT acquire
        req_resp = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert req_resp.status_code == 202
        assert req_resp.json()["status"] == "available"
        assert req_resp.json()["created"] is False
        assert calls == 0

        # 4. Attempt force=true without authorization: rejected
        force_resp = await client.post(f"/processes/{CNJ}/request?force=true", headers=headers)
        assert force_resp.status_code == 409
        assert "force=true is not sufficient authorization" in force_resp.json()["detail"]
        assert calls == 0

        # 5. Refresh with valid authorization archives old request to history and initiates new request
        auth_resp = await client.post(
            f"/processes/{CNJ}/authorizations",
            headers=headers,
            json={"operation": "refresh"},
        )
        assert auth_resp.status_code == 201
        refresh_token = auth_resp.json()["token"]

        refresh_req = await client.post(
            f"/processes/{CNJ}/request",
            headers={**headers, "X-Acquisition-Authorization": refresh_token},
        )
        assert refresh_req.status_code == 202
        assert refresh_req.json()["created"] is True

        # Verify old request was preserved in tenant_judit_request_history
        async with pool.acquire() as conn:
            hist_row = await conn.fetchrow(
                "SELECT judit_request_id, status FROM tenant_judit_request_history WHERE tenant_id=$1 AND process_code=$2",
                tenant_id,
                CNJ,
            )
            assert hist_row is not None
            assert hist_row["judit_request_id"] == "req-hist-1"
            assert hist_row["status"] == "completed"

    await pool.close()


@pytest.mark.asyncio
async def test_3_two_simultaneous_requests(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fake_create(code):
        nonlocal calls
        calls += 1
        return JuditRequestResult(request_id="req-concurrent")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp1, resp2 = await asyncio.gather(
            client.post(f"/processes/{CNJ}/request", headers=headers),
            client.post(f"/processes/{CNJ}/request", headers=headers),
        )
        assert resp1.status_code == resp2.status_code == 202
        created_flags = [resp1.json()["created"], resp2.json()["created"]]
        assert created_flags.count(True) == 1
        assert created_flags.count(False) == 1
        assert calls == 0

        assert await _worker(pool).process_one() is True
        assert calls == 1

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
            tenant_id,
            CNJ,
        )
        assert count == 1
    await pool.close()


@pytest.mark.asyncio
async def test_4_repetition_while_acquisition_in_progress(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fake_create(code):
        nonlocal calls
        calls += 1
        return JuditRequestResult(request_id="req-in-prog")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert first.status_code == 202 and first.json()["created"] is True

        # Pre-check detects acquisition in progress
        status_resp = await client.get(f"/processes/{CNJ}/status", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["classification"] == "acquisition_pending"

        # Repeating while in progress returns created=False without new call
        second = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert second.status_code == 202
        assert second.json()["created"] is False
        assert second.json()["status"] == "processing"
        assert calls == 0

        assert await _worker(pool).process_one() is True
        assert calls == 1

    await pool.close()


@pytest.mark.asyncio
async def test_5_ambiguous_failure(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fail_create(code):
        nonlocal calls
        calls += 1
        raise JuditRequestError("Provider connection timeout", retry_safe=False)

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fail_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert first.status_code == 202 and first.json()["created"] is True

        # Worker runs and encounters ambiguous failure
        assert await _worker(pool).process_one() is True
        assert calls == 1

        # Precheck classifies as ambiguous_failure
        status_resp = await client.get(f"/processes/{CNJ}/status", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["classification"] == "ambiguous_failure"

        # Subsequent acquisition requests are blocked with HTTP 409
        repeat_resp = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert repeat_resp.status_code == 409
        assert "ambiguous provider failure" in repeat_resp.json()["detail"]
        assert calls == 1

    await pool.close()


@pytest.mark.asyncio
async def test_6_retryable_failure(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def create(code):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise JuditRequestError("Judit 429 rate limited", retry_safe=True)
        return JuditRequestResult(request_id="req-recovered")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert first.status_code == 202 and first.json()["created"] is True
        assert await _worker(pool).process_one() is True
        assert calls == 1

        # Retryable failure allows repeat request to transition back to processing
        retry_resp = await client.post(f"/processes/{CNJ}/request", headers=headers)
        assert retry_resp.status_code == 202 and retry_resp.json()["created"] is True
        assert await _worker(pool).process_one() is True
        assert calls == 2

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, attempt_number, judit_request_id FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
            tenant_id,
            CNJ,
        )
        assert row["status"] == "processing"
        assert int(row["attempt_number"]) == 2
        assert row["judit_request_id"] == "req-recovered"
    await pool.close()


@pytest.mark.asyncio
async def test_7_tenant_isolation(monkeypatch):
    pool, tenant_1_id, token_1 = await _setup(monkeypatch)
    tenant_2_id = uuid4()
    token_2 = f"request-{uuid4()}"
    app.state.bearer_tokens[token_2] = tenant_2_id
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenants(id, name) VALUES($1, 'tenant-2')", tenant_2_id)

    # Tenant 1 acquires CNJ
    async def fake_create(code):
        return JuditRequestResult(request_id="req-t1")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        h1 = {"Authorization": f"Bearer {token_1}"}
        h2 = {"Authorization": f"Bearer {token_2}"}

        # Issue authorization for Tenant 1
        t1_auth = (await client.post(f"/processes/{CNJ}/authorizations", headers=h1)).json()["token"]
        await client.post(f"/processes/{CNJ}/request", headers={**h1, "X-Acquisition-Authorization": t1_auth})
        assert await _worker(pool).process_one() is True

        # Tenant 2 prechecks the same CNJ: must show acquisition_required and NO history
        status_t2 = await client.get(f"/processes/{CNJ}/status", headers=h2)
        assert status_t2.status_code == 200
        d2 = status_t2.json()
        assert d2["classification"] == "acquisition_required"
        assert d2["history_available"] is False
        assert d2["last_acquisition_at"] is None

        # Tenant 2 cannot use Tenant 1's authorization token
        hijack_resp = await client.post(
            f"/processes/{CNJ}/request",
            headers={**h2, "X-Acquisition-Authorization": t1_auth},
        )
        assert hijack_resp.status_code == 400

    await pool.close()


@pytest.mark.asyncio
async def test_8_attempt_to_reuse_authorization(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fake_create(code):
        nonlocal calls
        calls += 1
        return JuditRequestResult(request_id=f"req-{calls}")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # Issue single authorization
        auth_resp = await client.post(
            f"/processes/{CNJ}/authorizations",
            headers=headers,
            json={"operation": "acquisition"},
        )
        auth_token = auth_resp.json()["token"]

        # First use succeeds
        req1 = await client.post(
            f"/processes/{CNJ}/request",
            headers={**headers, "X-Acquisition-Authorization": auth_token},
        )
        assert req1.status_code == 202
        assert req1.json()["created"] is True
        assert await _worker(pool).process_one() is True
        assert calls == 1

        # Second use of same authorization token is rejected with 409
        req2 = await client.post(
            f"/processes/{CNJ}/request",
            headers={**headers, "X-Acquisition-Authorization": auth_token},
        )
        assert req2.status_code == 409
        assert "already been consumed" in req2.json()["detail"]
        assert calls == 1

    await pool.close()


@pytest.mark.asyncio
async def test_9_compatibility_with_v1_resumos(monkeypatch):
    pool, tenant_id, token = await _setup(monkeypatch)
    calls = 0

    async def fake_create(code):
        nonlocal calls
        calls += 1
        return JuditRequestResult(request_id="req-v1")

    monkeypatch.setattr(process_requests, "create_lawsuit_request", fake_create)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"idem-{uuid4()}",
            "Content-Type": "application/json",
        }
        body = {"cnj": CNJ, "format": "json"}

        # First call creates summary job
        resp1 = await client.post("/v1/resumos", headers=headers, json=body)
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]

        # Replay with same Idempotency-Key returns exact same job_id without duplicate acquisition
        resp2 = await client.post("/v1/resumos", headers=headers, json=body)
        assert resp2.status_code == 202
        assert resp2.json()["job_id"] == job_id_1
        assert calls == 0

        # Worker executes acquisition
        assert await _worker(pool).process_one() is True
        assert calls == 1

    await pool.close()
