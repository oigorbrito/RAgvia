from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

import app.api as api_module
from app.api import app
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _reset(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants
            RESTART IDENTITY CASCADE
            """
        )


@pytest.mark.asyncio
async def test_delivery_rolls_back_when_staging_fails_and_same_callback_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "atomic-webhook")
    await _reset(pool)

    original_stage_version = api_module.stage_version
    request_id = f"req-atomic-{uuid4()}"
    response_id = f"resp-atomic-{uuid4()}"
    callback_id = f"cb-atomic-{uuid4()}"
    code = "0000000-00.0000.0.00.0301"
    body = {
        "callback_id": callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {"code": code, "steps": []},
            "tags": {"cached_response": False},
        },
    }

    async def fail_stage(*args, **kwargs):
        raise RuntimeError("simulated staging failure")

    monkeypatch.setattr(api_module, "stage_version", fail_stage)
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(RuntimeError, match="simulated staging failure"):
                await client.post("/webhooks/judit/atomic-webhook", json=body)

            async with pool.acquire() as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM judit_deliveries WHERE callback_id = $1",
                    callback_id,
                ) == 0
                assert await conn.fetchval(
                    "SELECT count(*) FROM process_versions WHERE judit_response_id = $1",
                    response_id,
                ) == 0

            monkeypatch.setattr(api_module, "stage_version", original_stage_version)
            retried = await client.post("/webhooks/judit/atomic-webhook", json=body)
            assert retried.status_code == 200

        async with pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM judit_deliveries WHERE callback_id = $1",
                callback_id,
            ) == 1
            assert await conn.fetchval(
                "SELECT count(*) FROM process_versions WHERE judit_response_id = $1",
                response_id,
            ) == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_delivery_rolls_back_when_enqueue_fails_and_same_callback_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "atomic-webhook")
    await _reset(pool)

    original_enqueue = api_module.enqueue
    request_id = f"req-atomic-{uuid4()}"
    callback_id = f"cb-atomic-{uuid4()}"
    body = {
        "callback_id": callback_id,
        "event_type": "request_completed",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {"status": "completed"},
    }

    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("simulated enqueue failure")

    monkeypatch.setattr(api_module, "enqueue", fail_enqueue)
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(RuntimeError, match="simulated enqueue failure"):
                await client.post("/webhooks/judit/atomic-webhook", json=body)

            async with pool.acquire() as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM judit_deliveries WHERE callback_id = $1",
                    callback_id,
                ) == 0
                assert await conn.fetchval(
                    "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
                    f"judit-finalize:{request_id}",
                ) == 0

            monkeypatch.setattr(api_module, "enqueue", original_enqueue)
            retried = await client.post("/webhooks/judit/atomic-webhook", json=body)
            assert retried.status_code == 200

        async with pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM judit_deliveries WHERE callback_id = $1",
                callback_id,
            ) == 1
            assert await conn.fetchval(
                "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
                f"judit-finalize:{request_id}",
            ) == 1
    finally:
        await pool.close()
