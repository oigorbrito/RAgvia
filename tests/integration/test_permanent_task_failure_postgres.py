from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.queue import claim, enqueue, fail

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_permanent_failure_dead_letters_without_consuming_retry_budget() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    worker_id = uuid4()
    try:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
        enqueued = await enqueue(
            conn,
            task_name="integration-test",
            payload={"value": "permanent"},
            max_attempts=5,
            idempotency_key="integration:permanent-failure",
        )
        assert enqueued is not None
        claimed = await claim(conn, worker_id)
        assert claimed is not None
        assert int(claimed["attempts"]) == 1

        status = await fail(
            conn,
            claimed["id"],
            worker_id,
            attempts=1,
            error="PermanentTaskError: deterministic contract failure",
            permanent=True,
        )
        assert status == "dead"

        state = await conn.fetchrow(
            "SELECT status::text AS status, attempts, worker_id, error_log FROM jobs WHERE id = $1",
            claimed["id"],
        )
        assert state is not None
        assert state["status"] == "dead"
        assert int(state["attempts"]) == 1
        assert state["worker_id"] is None
        assert "deterministic contract failure" in state["error_log"]
    finally:
        await conn.close()
