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


@pytest.mark.asyncio
async def test_external_judit_summary_is_recorded_but_never_promoted_or_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants
            RESTART IDENTITY CASCADE
            """
        )

    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "summary-source-contract")
    request_id = f"req-{uuid4()}"
    callback_id = f"cb-{uuid4()}"
    response_id = f"resp-{uuid4()}"
    code = "0000000-00.2026.8.21.0110"
    external_summary = "EXTERNAL_SUMMARY_MUST_NEVER_BECOME_IASUMMARY"

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/judit/summary-source-contract",
                json={
                    "callback_id": callback_id,
                    "event_type": "response_created",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {
                        "request_id": request_id,
                        "response_id": response_id,
                        "response_type": "summary",
                        "response_data": {
                            "code": code,
                            "summary": external_summary,
                        },
                        "tags": {"cached_response": False},
                    },
                },
            )
        assert response.status_code == 200

        async with pool.acquire() as conn:
            delivery = await conn.fetchrow(
                """
                SELECT callback_id, event_type, raw_payload
                FROM judit_deliveries
                WHERE callback_id = $1
                """,
                callback_id,
            )
            process_count = await conn.fetchval(
                "SELECT count(*) FROM processes WHERE code = $1",
                code,
            )
            version_count = await conn.fetchval("SELECT count(*) FROM process_versions")
            summary_count = await conn.fetchval(
                "SELECT count(*) FROM process_summaries WHERE markdown LIKE $1",
                f"%{external_summary}%",
            )
            generation_jobs = await conn.fetchval(
                "SELECT count(*) FROM jobs WHERE task_name = 'generate_process_summary'"
            )

        assert delivery is not None
        assert delivery["callback_id"] == callback_id
        assert delivery["event_type"] == "response_created"
        assert delivery["raw_payload"]["payload"]["response_type"] == "summary"
        assert external_summary in str(delivery["raw_payload"])
        assert process_count == 0
        assert version_count == 0
        assert summary_count == 0
        assert generation_jobs == 0
    finally:
        await pool.close()
