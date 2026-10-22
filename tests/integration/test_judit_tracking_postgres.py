from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app.db import create_pool
from app.migrations import migrate
from app.tracking_scheduler import enqueue_due_tracking_reconciliations

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

CODE_A = "0000000-00.2026.8.21.1471"
CODE_B = "0000000-00.2026.8.21.1472"


async def _reset(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE judit_tracking_refreshes, judit_trackings,
                     public_summary_requests, jobs, judit_deliveries,
                     judit_request_completions, process_summaries, process_steps,
                     tenant_processes, tenant_judit_requests, access_log,
                     process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )


@pytest.fixture
async def tracking_client(monkeypatch: pytest.MonkeyPatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=8)
    await _reset(pool)
    async with pool.acquire() as conn:
        tenant_a = await conn.fetchval(
            "INSERT INTO tenants (name) VALUES ('tracking tenant A') RETURNING id"
        )
        tenant_b = await conn.fetchval(
            "INSERT INTO tenants (name) VALUES ('tracking tenant B') RETURNING id"
        )
    app.state.pool = pool
    app.state.bearer_tokens = {"tracking-a": tenant_a, "tracking-b": tenant_b}
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "tracking-webhook")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, pool, tenant_a, tenant_b
    await pool.close()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tracking_event(*, tracking_id: str, request_id: str, response_id: str) -> dict:
    return {
        "callback_id": f"callback-{uuid4()}",
        "event_type": "response_created",
        "reference_type": "tracking",
        "reference_id": tracking_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {"code": CODE_A, "steps": []},
            "tags": {"cached_response": False},
        },
    }


@pytest.mark.asyncio
async def test_tracking_create_list_delete_is_tenant_scoped_and_starts_initial_query(
    tracking_client,
) -> None:
    client, pool, _tenant_a, _tenant_b = tracking_client

    created = await client.post(
        f"/v1/trackings/{CODE_A}",
        headers=_headers("tracking-a"),
    )
    assert created.status_code == 202
    tracking_id = created.json()["tracking_id"]
    assert created.json()["created"] is True

    listed_a = await client.get("/v1/trackings", headers=_headers("tracking-a"))
    listed_b = await client.get("/v1/trackings", headers=_headers("tracking-b"))
    assert [item["tracking_id"] for item in listed_a.json()["trackings"]] == [tracking_id]
    assert listed_b.json()["trackings"] == []

    cross_tenant_delete = await client.delete(
        f"/v1/trackings/{tracking_id}",
        headers=_headers("tracking-b"),
    )
    assert cross_tenant_delete.status_code == 404

    async with pool.acquire() as conn:
        task_names = set(
            await conn.fetchval(
                "SELECT array_agg(task_name ORDER BY task_name) FROM jobs"
            )
            or []
        )
    assert {"create_judit_tracking", "request_judit_process"}.issubset(task_names)

    removed = await client.delete(
        f"/v1/trackings/{tracking_id}",
        headers=_headers("tracking-a"),
    )
    assert removed.status_code == 202
    assert removed.json()["status"] == "deleting"


@pytest.mark.asyncio
async def test_tracking_callback_binds_tenant_and_deduplicates_response(tracking_client) -> None:
    client, pool, tenant_a, _tenant_b = tracking_client
    provider_tracking_id = f"tracking-{uuid4()}"
    request_id = f"request-{uuid4()}"
    response_id = f"response-{uuid4()}"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO judit_trackings (
                tenant_id, process_code, provider_tracking_id, status, recurrence_days
            )
            VALUES ($1, $2, $3, 'active', 1)
            """,
            tenant_a,
            CODE_A,
            provider_tracking_id,
        )

    body = _tracking_event(
        tracking_id=provider_tracking_id,
        request_id=request_id,
        response_id=response_id,
    )
    first = await client.post("/webhooks/judit/tracking-webhook", json=body)
    body["callback_id"] = f"callback-{uuid4()}"
    second = await client.post("/webhooks/judit/tracking-webhook", json=body)
    assert first.status_code == 200
    assert second.status_code == 200

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS versions,
                   count(tp.process_id) AS tenant_links,
                   max(jt.last_event_at) AS last_event_at
            FROM process_versions pv
            JOIN processes p ON p.id = pv.process_id
            LEFT JOIN tenant_processes tp
              ON tp.process_id = p.id AND tp.tenant_id = $1
            JOIN judit_trackings jt
              ON jt.provider_tracking_id = $2
            WHERE p.code = $3 AND pv.judit_response_id = $4
            """,
            tenant_a,
            provider_tracking_id,
            CODE_A,
            response_id,
        )
    assert int(row["versions"]) == 1
    assert int(row["tenant_links"]) == 1
    assert row["last_event_at"] is not None


@pytest.mark.asyncio
async def test_stale_tracking_reconciliation_is_once_per_window(tracking_client) -> None:
    _client, pool, tenant_a, _tenant_b = tracking_client
    tracking_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO judit_trackings (
                id, tenant_id, process_code, provider_tracking_id, status,
                recurrence_days, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, 'active', 1, NOW() - INTERVAL '10 days', NOW())
            """,
            tracking_id,
            tenant_a,
            CODE_B,
            f"provider-{uuid4()}",
        )
        async with conn.transaction():
            first = await enqueue_due_tracking_reconciliations(
                conn,
                stale_after=timedelta(hours=36),
                limit=25,
            )
        async with conn.transaction():
            second = await enqueue_due_tracking_reconciliations(
                conn,
                stale_after=timedelta(hours=36),
                limit=25,
            )
        refresh_count = await conn.fetchval(
            "SELECT count(*) FROM judit_tracking_refreshes WHERE tracking_id=$1",
            tracking_id,
        )
        job_count = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name='refresh_judit_tracking'"
        )

    assert first == 1
    assert second == 0
    assert refresh_count == 1
    assert job_count == 1


@pytest.mark.asyncio
async def test_request_completion_closes_tracking_refresh(tracking_client) -> None:
    _client, pool, tenant_a, _tenant_b = tracking_client
    request_id = f"refresh-{uuid4()}"
    async with pool.acquire() as conn:
        tracking_id = await conn.fetchval(
            """
            INSERT INTO judit_trackings (
                tenant_id, process_code, provider_tracking_id, status, recurrence_days
            )
            VALUES ($1, $2, $3, 'active', 1)
            RETURNING id
            """,
            tenant_a,
            CODE_B,
            f"provider-{uuid4()}",
        )
        refresh_id = await conn.fetchval(
            """
            INSERT INTO judit_tracking_refreshes (
                tracking_id, judit_request_id, status
            )
            VALUES ($1, $2, 'processing')
            RETURNING id
            """,
            tracking_id,
            request_id,
        )
        await conn.execute(
            "INSERT INTO judit_request_completions (request_id) VALUES ($1)",
            request_id,
        )
        status = await conn.fetchval(
            "SELECT status FROM judit_tracking_refreshes WHERE id=$1",
            refresh_id,
        )
    assert status == "completed"
