from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.queue import MAX_JOB_ERROR_LOG_CHARS, claim, enqueue, fail, reclaim_stale

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_fail_keeps_recent_error_log_tail_with_hard_bound() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    worker_id = uuid4()
    try:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
        row = await enqueue(
            conn,
            task_name="integration-test",
            payload={},
            max_attempts=100,
            idempotency_key="integration:error-log-bound-fail",
        )
        assert row is not None
        claimed = await claim(conn, worker_id)
        assert claimed is not None

        old_prefix = "OLD-MARKER-" + ("x" * MAX_JOB_ERROR_LOG_CHARS)
        await conn.execute(
            "UPDATE jobs SET error_log = $2 WHERE id = $1",
            claimed["id"],
            old_prefix,
        )
        status = await fail(
            conn,
            claimed["id"],
            worker_id,
            attempts=int(claimed["attempts"]),
            error="NEWEST-FAILURE-MARKER",
        )
        error_log = str(
            await conn.fetchval("SELECT error_log FROM jobs WHERE id = $1", claimed["id"])
        )

        assert status == "pending"
        assert len(error_log) <= MAX_JOB_ERROR_LOG_CHARS
        assert "NEWEST-FAILURE-MARKER" in error_log
        assert "OLD-MARKER" not in error_log
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reclaim_keeps_recent_error_log_tail_with_same_bound() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    worker_id = uuid4()
    try:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
        row = await enqueue(
            conn,
            task_name="integration-test",
            payload={},
            max_attempts=100,
            idempotency_key="integration:error-log-bound-reclaim",
        )
        assert row is not None
        claimed = await claim(conn, worker_id)
        assert claimed is not None

        await conn.execute(
            """
            UPDATE jobs
            SET error_log = $2,
                last_heartbeat = NOW() - interval '10 minutes'
            WHERE id = $1
            """,
            claimed["id"],
            "OLD-MARKER-" + ("y" * MAX_JOB_ERROR_LOG_CHARS),
        )
        reclaimed = await reclaim_stale(conn, timeout_seconds=30)
        error_log = str(
            await conn.fetchval("SELECT error_log FROM jobs WHERE id = $1", claimed["id"])
        )

        assert any(item["id"] == claimed["id"] for item in reclaimed)
        assert len(error_log) <= MAX_JOB_ERROR_LOG_CHARS
        assert "Worker heartbeat timed out." in error_log
        assert "OLD-MARKER" not in error_log
    finally:
        await conn.close()
