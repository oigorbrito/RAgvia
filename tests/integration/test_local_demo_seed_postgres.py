from __future__ import annotations

import os

import asyncpg
import pytest

from app.migrations import migrate
from app.local_demo import DEMO_CODE, seed_demo

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_local_demo_seed_is_provider_free_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    tenant_id = "00000000-0000-0000-0000-000000000151"
    monkeypatch.setenv(
        "RPY_BEARER_TOKENS",
        '{"dev-local-token":"00000000-0000-0000-0000-000000000151"}',
    )
    monkeypatch.delenv("JUDIT_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, judit_request_completions,
                     tenant_judit_requests, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants RESTART IDENTITY CASCADE
            """
        )
    finally:
        await conn.close()

    await seed_demo(TEST_DATABASE_URL)
    await seed_demo(TEST_DATABASE_URL)

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        row = await conn.fetchrow(
            """
            SELECT p.id, p.current_version_id, ps.model, ps.cost_usd,
                   COALESCE((ps.validation->>'passed')::boolean, false) AS passed
            FROM processes p
            JOIN tenant_processes tp ON tp.process_id = p.id
            JOIN process_summaries ps
              ON ps.process_id = p.id AND ps.version_id = p.current_version_id
            WHERE p.code = $1 AND tp.tenant_id = $2::uuid
            """,
            DEMO_CODE,
            tenant_id,
        )
        assert row is not None
        assert row["current_version_id"] is not None
        assert row["model"] == "local-demo-no-provider"
        assert float(row["cost_usd"]) == 0.0
        assert row["passed"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM processes WHERE code = $1", DEMO_CODE
        ) == 1
    finally:
        await conn.close()
