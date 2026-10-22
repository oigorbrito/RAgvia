from __future__ import annotations

import os

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

CODE_A = "0000000-00.2026.8.21.1473"
CODE_B = "0000000-00.2026.8.21.1474"


@pytest.mark.asyncio
async def test_batch_tracking_deduplicates_codes_and_enqueues_initial_acquisition(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    try:
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
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('tracking batch tenant') RETURNING id"
            )
        app.state.bearer_tokens = {"tracking-batch": tenant_id}
        monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "tracking-webhook")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/trackings",
                headers={"Authorization": "Bearer tracking-batch"},
                json={
                    "codes": [CODE_A, CODE_B, CODE_A],
                    "recurrence_days": 7,
                },
            )
        assert response.status_code == 202
        payload = response.json()["trackings"]
        assert [item["code"] for item in payload] == [CODE_A, CODE_B]
        assert all(item["created"] is True for item in payload)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT process_code, recurrence_days
                FROM judit_trackings
                WHERE tenant_id = $1
                ORDER BY process_code
                """,
                tenant_id,
            )
            acquisition_jobs = await conn.fetchval(
                "SELECT count(*) FROM jobs WHERE task_name = 'request_judit_process'"
            )
            tracking_jobs = await conn.fetchval(
                "SELECT count(*) FROM jobs WHERE task_name = 'create_judit_tracking'"
            )

        assert [(row["process_code"], row["recurrence_days"]) for row in rows] == [
            (CODE_A, 7),
            (CODE_B, 7),
        ]
        assert acquisition_jobs == 2
        assert tracking_jobs == 2
    finally:
        await pool.close()
