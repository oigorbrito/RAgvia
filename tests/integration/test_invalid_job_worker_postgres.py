from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.migrations import migrate
from app.queue import enqueue
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_invalid_task_is_dead_and_next_healthcheck_completes() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    worker_id = uuid4()
    worker = Worker(
        pool,
        WorkerSettings(
            database_url=TEST_DATABASE_URL,
            concurrency=1,
            heartbeat_interval_seconds=1,
            stale_after_seconds=2,
            task_timeout_seconds=2,
            reclaim_interval_seconds=1,
            shutdown_grace_seconds=1,
        ),
        worker_id=worker_id,
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
            invalid = await enqueue(
                conn,
                task_name="unknown-integration-task",
                payload={"value": 1},
                priority=1,
                max_attempts=5,
                idempotency_key="integration:invalid-task",
            )
            healthy = await enqueue(
                conn,
                task_name="healthcheck",
                payload={"sequence": "next"},
                priority=2,
                idempotency_key="integration:healthcheck-after-invalid",
            )
        assert invalid is not None
        assert healthy is not None

        assert await worker.process_one() is True
        assert await worker.process_one() is True

        async with pool.acquire() as conn:
            invalid_state = await conn.fetchrow(
                "SELECT status::text AS status, attempts, worker_id, error_log FROM jobs WHERE id = $1",
                invalid["id"],
            )
            healthy_state = await conn.fetchrow(
                "SELECT status::text AS status, attempts, worker_id, result FROM jobs WHERE id = $1",
                healthy["id"],
            )

        assert invalid_state is not None
        assert invalid_state["status"] == "dead"
        assert int(invalid_state["attempts"]) == 1
        assert invalid_state["worker_id"] is None
        assert "invalid job contract" in str(invalid_state["error_log"])
        assert "unknown task" in str(invalid_state["error_log"])

        assert healthy_state is not None
        assert healthy_state["status"] == "completed"
        assert int(healthy_state["attempts"]) == 1
        assert healthy_state["worker_id"] == worker_id
        assert healthy_state["result"]["ok"] is True
        assert healthy_state["result"]["payload"]["sequence"] == "next"
    finally:
        await pool.close()
