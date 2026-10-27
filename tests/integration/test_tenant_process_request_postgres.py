from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest

import app.process_requests as process_requests
from app.api import app
from app.db import create_pool
from app.judit_client import JuditRequestError, JuditRequestResult
from app.migrations import migrate
from app.public_lifecycle import create_or_get_summary_request, request_fingerprint
from app.process_query_safety import AmbiguousFailureError
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
CNJ = "0000000-00.2024.8.26.0100"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "judit" / "tracking_lawsuit_response.json"

async def _setup(monkeypatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL); pool = await create_pool(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS", "100")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "false")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", "APR-SYNTHETIC-" + uuid4().hex.upper())
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE jobs, judit_one_shot_call_reservations, judit_deliveries, judit_request_completions, process_summaries, process_steps, tenant_processes, tenant_judit_requests, access_log, process_versions, processes, tenants RESTART IDENTITY CASCADE")
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
            reserved=await conn.fetchval("SELECT provider_calls_reserved FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",tenant_id,CNJ)
            job=await conn.fetchrow("SELECT status::text AS status, attempts, max_attempts FROM jobs WHERE task_name='request_judit_process'")
        assert request_status=="failed_ambiguous" and int(reserved)==1
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

@pytest.mark.asyncio
async def test_one_call_budget_is_reserved_and_not_retried_after_retryable_rejection(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch); calls=0
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS","1")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE","true")
    async def reject_once(code):
        nonlocal calls; calls+=1
        raise JuditRequestError("Judit request failed with HTTP 429", retry_safe=True)
    monkeypatch.setattr(process_requests,"create_lawsuit_request",reject_once)
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
            row=await conn.fetchrow(
                "SELECT status, max_provider_calls, provider_calls_reserved, last_provider_call_reserved_at FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
                tenant_id,CNJ,
            )
            jobs=await conn.fetch("SELECT status::text AS status, attempts, max_attempts FROM jobs WHERE task_name='request_judit_process'")
        assert row and row["status"]=="failed_retryable"
        assert int(row["max_provider_calls"])==1 and int(row["provider_calls_reserved"])==1
        assert row["last_provider_call_reserved_at"] is not None
        assert len(jobs)==1 and jobs[0]["status"]=="dead" and int(jobs[0]["max_attempts"])==1
        assert calls==1
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.PostgresError, match="cannot be refunded"):
                await conn.execute(
                    "UPDATE tenant_judit_requests SET provider_calls_reserved=0 WHERE tenant_id=$1 AND process_code=$2",
                    tenant_id,CNJ,
                )
            with pytest.raises(asyncpg.PostgresError, match="cannot be expanded"):
                await conn.execute(
                    "UPDATE tenant_judit_requests SET max_provider_calls=2 WHERE tenant_id=$1 AND process_code=$2",
                    tenant_id,CNJ,
                )
    finally:
        await pool.close()

@pytest.mark.asyncio
async def test_concurrent_duplicate_tasks_cannot_bypass_persisted_budget(monkeypatch):
    pool,tenant_id,_=await _setup(monkeypatch); calls=0
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS","1")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE","true")
    async with pool.acquire() as conn:
        public_request,_=await create_or_get_summary_request(
            conn,
            tenant_id=tenant_id,
            process_code=CNJ,
            idempotency_key="duplicate-budget-synthetic",
            fingerprint=request_fingerprint({"cnj":CNJ,"synthetic":True}),
        )
    result=await process_requests.request_process(
        pool,tenant_id=tenant_id,code=CNJ,public_summary_request_id=public_request.id
    )
    assert result.created is True
    async with pool.acquire() as conn:
        tenant_request_id=await conn.fetchval(
            "SELECT id FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
            tenant_id,CNJ,
        )
    async def fake_create(code):
        nonlocal calls; calls+=1
        await asyncio.sleep(0.05)
        return JuditRequestResult(request_id="req-concurrent-budget")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_create)
    payload={"tenant_request_id":str(tenant_request_id)}
    try:
        await asyncio.gather(
            process_requests.request_judit_process_task(payload),
            process_requests.request_judit_process_task(payload),
        )
        async with pool.acquire() as conn:
            row=await conn.fetchrow(
                "SELECT status, max_provider_calls, provider_calls_reserved, judit_request_id FROM tenant_judit_requests WHERE id=$1",
                tenant_request_id,
            )
            public_status=await conn.fetchval(
                "SELECT status::text FROM public_summary_requests WHERE id=$1",
                public_request.id,
            )
        assert row and row["judit_request_id"]=="req-concurrent-budget"
        assert public_status=="fetching"
        assert int(row["max_provider_calls"])==1 and int(row["provider_calls_reserved"])==1
        assert calls==1
    finally:
        await pool.close()

@pytest.mark.asyncio
async def test_one_shot_run_does_not_reuse_an_existing_provider_attempt(monkeypatch):
    pool,tenant_id,_=await _setup(monkeypatch)
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS","1")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE","true")
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenant_judit_requests (
                    tenant_id, process_code, judit_request_id, status, attempt_number,
                    max_provider_calls, provider_calls_reserved, last_provider_call_reserved_at
                ) VALUES ($1,$2,NULL,'failed_retryable',1,100,1,NOW())
                """,
                tenant_id,
                CNJ,
            )
        result=await process_requests.request_process(pool,tenant_id=tenant_id,code=CNJ)
        assert result.budget_exhausted and not result.created
        async with pool.acquire() as conn:
            job_count=await conn.fetchval(
                "SELECT count(*) FROM jobs WHERE task_name='request_judit_process'"
            )
        assert job_count==0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_global_one_shot_run_budget_blocks_a_second_process(monkeypatch):
    pool,tenant_id,_=await _setup(monkeypatch); calls=[]
    run_id=os.environ.get("PROVIDER_ONE_SHOT_RUN_ID")
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS","1")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE","true")
    other_code=CNJ.replace("0100","0101")
    first=await process_requests.request_process(pool,tenant_id=tenant_id,code=CNJ)
    second=await process_requests.request_process(pool,tenant_id=tenant_id,code=other_code)
    assert first.created and second.created
    async with pool.acquire() as conn:
        rows=await conn.fetch(
            "SELECT id, process_code, status, provider_calls_reserved, judit_request_id FROM tenant_judit_requests WHERE tenant_id=$1 ORDER BY process_code",
            tenant_id,
        )
    async def fake_create(code):
        calls.append(code)
        await asyncio.sleep(0.05)
        return JuditRequestResult(request_id=f"req-{code[-4:]}")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_create)
    try:
        await asyncio.gather(*[
            process_requests.request_judit_process_task({"tenant_request_id":str(row["id"])})
            for row in rows
        ])
        async with pool.acquire() as conn:
            reservation=await conn.fetchrow(
                "SELECT tenant_request_id FROM judit_one_shot_call_reservations WHERE run_id=$1",
                run_id,
            )
            rows=await conn.fetch(
                "SELECT id, process_code, status, provider_calls_reserved, judit_request_id FROM tenant_judit_requests WHERE tenant_id=$1 ORDER BY process_code",
                tenant_id,
            )
            reservation_count=await conn.fetchval(
                "SELECT count(*) FROM judit_one_shot_call_reservations WHERE run_id=$1",
                run_id,
            )
        assert reservation is not None
        assert len(calls)==1 and calls[0] in {CNJ,other_code}
        assert len(rows)==2
        winner=next(row for row in rows if row["process_code"]==calls[0])
        loser=next(row for row in rows if row["process_code"]!=calls[0])
        assert int(winner["provider_calls_reserved"])==1 and winner["judit_request_id"] is not None
        assert reservation["tenant_request_id"]==winner["id"]
        assert loser["status"]=="failed_retryable" and int(loser["provider_calls_reserved"])==0
        async with pool.acquire() as conn:
            blocked_public,_=await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=loser["process_code"],
                idempotency_key="global-budget-block-synthetic",
                fingerprint=request_fingerprint({"cnj":loser["process_code"],"synthetic":True}),
            )
        blocked=await process_requests.request_process(
            pool,
            tenant_id=tenant_id,
            code=loser["process_code"],
            public_summary_request_id=blocked_public.id,
        )
        assert blocked.budget_exhausted and not blocked.created
        async with pool.acquire() as conn:
            blocked_public_status=await conn.fetchval(
                "SELECT status::text FROM public_summary_requests WHERE id=$1",
                blocked_public.id,
            )
        assert blocked_public_status=="source_unavailable"
        async with pool.acquire() as conn:
            wrong_public,_=await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=winner["process_code"],
                idempotency_key="wrong-scope-budget-synthetic",
                fingerprint=request_fingerprint({"cnj":winner["process_code"],"synthetic":True}),
            )
        with pytest.raises(process_requests.PermanentTaskError, match="scope mismatch"):
            await process_requests.request_process(
                pool,
                tenant_id=tenant_id,
                code=loser["process_code"],
                public_summary_request_id=wrong_public.id,
            )
        assert reservation_count==1
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tenant_judit_requests WHERE id=$1", winner["id"])
            assert await conn.fetchval(
                "SELECT tenant_request_id FROM judit_one_shot_call_reservations WHERE run_id=$1",
                run_id,
            ) is None
            with pytest.raises(asyncpg.PostgresError, match="reservation is immutable"):
                await conn.execute(
                    "DELETE FROM judit_one_shot_call_reservations WHERE run_id=$1",
                    run_id,
                )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_timeout_consumes_one_shot_budget_across_task_restart(monkeypatch):
    pool,tenant_id,_=await _setup(monkeypatch); calls=0
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS","1")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE","true")
    result=await process_requests.request_process(pool,tenant_id=tenant_id,code=CNJ)
    assert result.created is True
    async with pool.acquire() as conn:
        request_id=await conn.fetchval(
            "SELECT id FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
            tenant_id,CNJ,
        )
    async def timeout_after_send(code):
        nonlocal calls; calls+=1
        raise TimeoutError("synthetic ambiguous response timeout")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",timeout_after_send)
    payload={"tenant_request_id":str(request_id)}
    try:
        with pytest.raises(TimeoutError):
            await process_requests.request_judit_process_task(payload)
        async with pool.acquire() as conn:
            reserved=await conn.fetchval(
                "SELECT provider_calls_reserved FROM tenant_judit_requests WHERE id=$1",
                request_id,
            )
        assert int(reserved)==1
        assert (await process_requests.request_judit_process_task(payload))["status"]=="call_budget_exhausted"
        with pytest.raises(AmbiguousFailureError):
            await process_requests.request_process(pool,tenant_id=tenant_id,code=CNJ)
        async with pool.acquire() as conn:
            row=await conn.fetchrow(
                "SELECT status, max_provider_calls, provider_calls_reserved FROM tenant_judit_requests WHERE id=$1",
                request_id,
            )
        assert row and row["status"]=="failed_ambiguous"
        assert int(row["max_provider_calls"])==1 and int(row["provider_calls_reserved"])==1
        assert calls==1
    finally:
        await pool.close()
