from __future__ import annotations

import json
import os
from copy import deepcopy
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
    await migrate(TEST_DATABASE_URL); pool = await create_pool(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE jobs, judit_deliveries, judit_request_completions, process_summaries, process_steps, tenant_processes, tenant_judit_requests, access_log, process_versions, processes, tenants RESTART IDENTITY CASCADE")
    tenant_id=uuid4(); token=f"request-{uuid4()}"; app.state.bearer_tokens={token:tenant_id}; app.state.pool=pool
    async with pool.acquire() as conn: await conn.execute("INSERT INTO tenants(id,name) VALUES($1,'tenant')",tenant_id)
    return pool,tenant_id,token

def _worker(pool):
    assert TEST_DATABASE_URL is not None
    return Worker(pool, WorkerSettings(database_url=TEST_DATABASE_URL, concurrency=1, heartbeat_interval_seconds=60, stale_after_seconds=120, task_timeout_seconds=20, reclaim_interval_seconds=60))

@pytest.mark.asyncio
async def test_request_is_durable_idempotent_and_grants_callback_process(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch); calls=0
    async def fake_create(code):
        nonlocal calls; calls+=1; assert code==CNJ
        return JuditRequestResult(request_id="req-tenant-1")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_create)
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        headers={"Authorization":f"Bearer {token}"}
        first=await client.post(f"/processes/{CNJ}/request",headers=headers); second=await client.post(f"/processes/{CNJ}/request",headers=headers)
        assert first.status_code==second.status_code==202; assert first.json()["created"] is True; assert second.json()["created"] is False; assert calls==0
        assert await _worker(pool).process_one() is True; assert calls==1
        payload=deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))); payload["callback_id"]="cb-tenant-1"; payload["payload"]["request_id"]="req-tenant-1"; payload["payload"]["response_id"]="resp-tenant-1"; payload["payload"]["response_data"]["code"]=CNJ
        monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN","webhook-secret")
        callback=await client.post("/webhooks/judit/webhook-secret",json=payload); assert callback.status_code==200
    async with pool.acquire() as conn:
        count=await conn.fetchval("SELECT count(*) FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",tenant_id,CNJ)
        granted=await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_processes tp JOIN processes p ON p.id=tp.process_id WHERE tp.tenant_id=$1 AND p.code=$2)",tenant_id,CNJ)
        audit=await conn.fetchval("SELECT count(*) FROM access_log WHERE tenant_id=$1 AND action='request_process'",tenant_id)
    assert count==1 and granted and audit==2; await pool.close()

@pytest.mark.asyncio
async def test_existing_process_does_not_grant_cross_tenant_access(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch); calls=0
    async def fake_create(code):
        nonlocal calls; calls+=1; return JuditRequestResult(request_id="req-isolated")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_create)
    owner=uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenants(id,name) VALUES($1,'owner')",owner)
        process_id=await conn.fetchval("INSERT INTO processes(code) VALUES($1) RETURNING id",CNJ)
        await conn.execute("INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)",owner,process_id)
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        response=await client.post(f"/processes/{CNJ}/request",headers={"Authorization":f"Bearer {token}"})
    assert response.status_code==202; assert response.json()["status"]=="processing"; assert response.json()["created"] is True; assert calls==0
    async with pool.acquire() as conn: assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_processes WHERE tenant_id=$1)",tenant_id)
    assert await _worker(pool).process_one() is True; assert calls==1; await pool.close()

@pytest.mark.asyncio
async def test_callback_before_provider_mapping_is_reconciled(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch)
    callback_sent=False
    async def fake_create(code):
        nonlocal callback_sent
        payload=deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))); payload["callback_id"]="cb-early"; payload["payload"]["request_id"]="req-early"; payload["payload"]["response_id"]="resp-early"; payload["payload"]["response_data"]["code"]=CNJ
        monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN","webhook-secret")
        transport=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,base_url="http://test") as client: assert (await client.post("/webhooks/judit/webhook-secret",json=payload)).status_code==200
        callback_sent=True
        return JuditRequestResult(request_id="req-early")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_create)
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client: assert (await client.post(f"/processes/{CNJ}/request",headers={"Authorization":f"Bearer {token}"})).status_code==202
    assert await _worker(pool).process_one() is True; assert callback_sent
    async with pool.acquire() as conn: assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_processes tp JOIN processes p ON p.id=tp.process_id WHERE tp.tenant_id=$1 AND p.code=$2)",tenant_id,CNJ)
    await pool.close()

@pytest.mark.asyncio
async def test_ambiguous_provider_failure_is_terminal_and_not_automatically_retried(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch); calls=0
    async def fail_create(code):
        nonlocal calls; calls+=1; assert code==CNJ
        raise JuditRequestError("Judit request failed")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fail_create)
    transport=httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
            headers={"Authorization":f"Bearer {token}"}
            first=await client.post(f"/processes/{CNJ}/request",headers=headers)
            assert first.status_code==202 and first.json()["created"] is True
            assert await _worker(pool).process_one() is True
            repeated=await client.post(f"/processes/{CNJ}/request",headers=headers)
            assert repeated.status_code==409
        async with pool.acquire() as conn:
            request_status=await conn.fetchval("SELECT status FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",tenant_id,CNJ)
            job=await conn.fetchrow("SELECT status::text AS status, attempts, max_attempts FROM jobs WHERE task_name='request_judit_process'")
        assert request_status=="failed_ambiguous"
        assert job and job["status"]=="dead" and int(job["attempts"])==1 and int(job["max_attempts"])==1
        assert calls==1
    finally:
        await pool.close()

@pytest.mark.asyncio
async def test_explicit_provider_rejection_can_be_retried_without_new_request_row(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch); calls=0
    async def create(code):
        nonlocal calls; calls+=1
        if calls == 1: raise JuditRequestError("Judit request failed with HTTP 429", retry_safe=True)
        return JuditRequestResult(request_id="req-recovered")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",create)
    transport=httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
            headers={"Authorization":f"Bearer {token}"}
            first=await client.post(f"/processes/{CNJ}/request",headers=headers)
            assert first.status_code==202 and first.json()["created"] is True
            assert await _worker(pool).process_one() is True
            retry=await client.post(f"/processes/{CNJ}/request",headers=headers)
            assert retry.status_code==202 and retry.json()["created"] is True
            assert await _worker(pool).process_one() is True
        async with pool.acquire() as conn:
            row=await conn.fetchrow("SELECT status, attempt_number, judit_request_id FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",tenant_id,CNJ)
            jobs=await conn.fetch("SELECT status::text AS status FROM jobs WHERE task_name='request_judit_process' ORDER BY created_at")
        assert row and row["status"]=="processing" and int(row["attempt_number"])==2 and row["judit_request_id"]=="req-recovered"
        assert [job["status"] for job in jobs]==["dead","completed"]
        assert calls==2
    finally:
        await pool.close()
