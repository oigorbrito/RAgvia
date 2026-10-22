from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
async def api_client(monkeypatch: pytest.MonkeyPatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "integration-webhook")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, pool
    await pool.close()


@pytest.mark.asyncio
async def test_late_lawsuit_reenqueues_finalize_after_completion_was_consumed(api_client) -> None:
    client, pool = api_client
    request_id = f"req-{uuid4()}"
    completion_callback = f"cb-complete-{uuid4()}"
    response_id = f"resp-{uuid4()}"
    lawsuit_callback = f"cb-lawsuit-{uuid4()}"
    code = "0000000-00.2026.8.21.0999"

    completion = await client.post(
        "/webhooks/judit/integration-webhook",
        json={
            "callback_id": completion_callback,
            "event_type": "request_completed",
            "reference_type": "request",
            "reference_id": request_id,
            "payload": {"status": "completed"},
        },
    )
    assert completion.status_code == 200

    async with pool.acquire() as conn:
        marker = await conn.fetchval(
            "SELECT request_id FROM judit_request_completions WHERE request_id = $1",
            request_id,
        )
        original_job = await conn.fetchrow(
            "SELECT id FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize:{request_id}",
        )
        assert marker == request_id
        assert original_job is not None
        await conn.execute(
            "UPDATE jobs SET status = 'completed', updated_at = NOW() WHERE id = $1",
            original_job["id"],
        )

    lawsuit = await client.post(
        "/webhooks/judit/integration-webhook",
        json={
            "callback_id": lawsuit_callback,
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
        },
    )
    assert lawsuit.status_code == 200

    async with pool.acquire() as conn:
        staged = await conn.fetchrow(
            "SELECT id FROM process_versions WHERE judit_response_id = $1",
            response_id,
        )
        repair = await conn.fetchrow(
            "SELECT status::text AS status, payload FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize-repair:{request_id}:{response_id}",
        )

    assert staged is not None
    assert repair is not None
    assert repair["status"] == "pending"
    assert repair["payload"]["request_id"] == request_id


@pytest.mark.asyncio
async def test_lawsuit_before_completion_does_not_create_repair_job(api_client) -> None:
    client, pool = api_client
    request_id = f"req-{uuid4()}"
    response_id = f"resp-{uuid4()}"
    code = "0000000-00.2026.8.21.0998"

    response = await client.post(
        "/webhooks/judit/integration-webhook",
        json={
            "callback_id": f"cb-{uuid4()}",
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
        },
    )
    assert response.status_code == 200

    async with pool.acquire() as conn:
        repair_count = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize-repair:{request_id}:{response_id}",
        )
    assert repair_count == 0


@pytest.mark.asyncio
async def test_exact_duplicate_callbacks_create_one_delivery_version_and_finalize_job(api_client) -> None:
    client, pool = api_client
    request_id = f"req-{uuid4()}"
    response_id = f"resp-{uuid4()}"
    code = f"0000000-00.2026.8.21.{str(uuid4().int)[-4:]}"
    lawsuit = {
        "callback_id": f"cb-lawsuit-{uuid4()}",
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
    completion = {
        "callback_id": f"cb-completion-{uuid4()}",
        "event_type": "request_completed",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {"status": "completed"},
    }

    for event in (lawsuit, lawsuit, completion, completion):
        response = await client.post(
            "/webhooks/judit/integration-webhook", json=event
        )
        assert response.status_code == 200

    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM judit_deliveries WHERE request_id = $1",
            request_id,
        ) == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM judit_request_completions WHERE request_id = $1",
            request_id,
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM process_versions WHERE judit_response_id = $1",
            response_id,
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize:{request_id}",
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM process_summaries ps JOIN process_versions pv ON pv.id = ps.version_id WHERE pv.judit_response_id = $1",
            response_id,
        ) == 0
