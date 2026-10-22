from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

import app.worker as worker_module
from app.db import create_pool
from app.migrations import migrate
from app.queue import claim, complete, enqueue, fail, heartbeat, reclaim_stale
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)


@pytest.fixture(autouse=True)
async def clean_jobs() -> None:
    assert TEST_DATABASE_URL is not None
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_only_one_worker_claims_a_job() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=4)
    try:
        async with pool.acquire() as conn:
            await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 1},
                idempotency_key="integration:claim-once",
            )

        worker_a = uuid4()
        worker_b = uuid4()

        async def do_claim(worker_id):
            async with pool.acquire() as conn:
                return await claim(conn, worker_id)

        first, second = await asyncio.gather(
            do_claim(worker_a),
            do_claim(worker_b),
        )
        claimed = [row for row in (first, second) if row is not None]
        assert len(claimed) == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_multiple_workers_claim_distinct_jobs_without_loss_or_duplicates() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=4, max_size=6)
    try:
        async with pool.acquire() as conn:
            for value in range(4):
                await enqueue(
                    conn,
                    task_name="integration-multi-worker",
                    payload={"value": value},
                    idempotency_key=f"integration:multi-worker:{value}",
                )

        workers = [uuid4(), uuid4()]

        async def claim_two(worker_id):
            async with pool.acquire() as conn:
                return [await claim(conn, worker_id), await claim(conn, worker_id)]

        batches = await asyncio.gather(*(claim_two(worker_id) for worker_id in workers))
        claimed = [row for batch in batches for row in batch if row is not None]

        assert len(claimed) == 4
        assert len({row["id"] for row in claimed}) == 4
        async with pool.acquire() as conn:
            counts = await conn.fetchrow(
                """
                SELECT count(*) FILTER (WHERE status = 'processing') AS processing,
                       count(*) AS total
                FROM jobs
                WHERE task_name = 'integration-multi-worker'
                """
            )
            owners = await conn.fetch(
                "SELECT id, worker_id FROM jobs WHERE task_name = 'integration-multi-worker'"
            )
        assert counts["processing"] == 4
        assert counts["total"] == 4
        assert {row["id"] for row in owners} == {row["id"] for row in claimed}
        assert all(row["worker_id"] in set(workers) for row in owners)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_idempotent_enqueue_does_not_duplicate_job() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            first = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 2},
                idempotency_key="integration:idem",
            )
            second = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 2},
                idempotency_key="integration:idem",
            )
        assert first is not None
        assert second is None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reclaimer_returns_stale_job_to_pending() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    worker_id = uuid4()
    try:
        async with pool.acquire() as conn:
            await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 3},
                idempotency_key="integration:reclaim",
            )
            row = await claim(conn, worker_id)
            assert row is not None
            stale_at = datetime.now(UTC) - timedelta(minutes=10)
            await conn.execute(
                "UPDATE jobs SET last_heartbeat = $2 WHERE id = $1",
                row["id"],
                stale_at,
            )
            reclaimed = await reclaim_stale(conn, timeout_seconds=30)
            status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", row["id"])

        assert any(item["id"] == row["id"] for item in reclaimed)
        assert str(status) == "pending"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reclaimed_job_fences_the_stale_worker() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    stale_worker = uuid4()
    replacement_worker = uuid4()
    try:
        async with pool.acquire() as conn:
            enqueued = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 33},
                idempotency_key="integration:crash-fencing",
                max_attempts=3,
            )
            assert enqueued is not None

            first = await claim(conn, stale_worker)
            assert first is not None
            assert int(first["attempts"]) == 1

            await conn.execute(
                "UPDATE jobs SET last_heartbeat = NOW() - interval '10 minutes' WHERE id = $1",
                first["id"],
            )
            reclaimed = await reclaim_stale(conn, timeout_seconds=30)
            assert any(item["id"] == first["id"] for item in reclaimed)

            second = await claim(conn, replacement_worker)
            assert second is not None
            assert second["id"] == first["id"]
            assert int(second["attempts"]) == 2

            # The crashed worker may wake up late, but ownership has changed. Its
            # heartbeat/complete/fail operations must be fenced by worker_id.
            assert await heartbeat(conn, first["id"], stale_worker) is False
            assert await complete(conn, first["id"], stale_worker, {"owner": "stale"}) is False
            assert (
                await fail(
                    conn,
                    first["id"],
                    stale_worker,
                    attempts=int(first["attempts"]),
                    error="late stale failure",
                )
                is None
            )

            assert await complete(
                conn, second["id"], replacement_worker, {"owner": "replacement"}
            ) is True
            final = await conn.fetchrow(
                "SELECT status::text AS status, attempts, worker_id, result FROM jobs WHERE id = $1",
                first["id"],
            )

        assert final is not None
        assert final["status"] == "completed"
        assert int(final["attempts"]) == 2
        assert final["worker_id"] == replacement_worker
        assert final["result"]["owner"] == "replacement"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reclaimer_dead_letters_crash_on_last_attempt() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    worker_id = uuid4()
    try:
        async with pool.acquire() as conn:
            enqueued = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 34},
                idempotency_key="integration:crash-dead",
                max_attempts=1,
            )
            assert enqueued is not None
            claimed = await claim(conn, worker_id)
            assert claimed is not None
            assert int(claimed["attempts"]) == 1

            await conn.execute(
                "UPDATE jobs SET last_heartbeat = NOW() - interval '10 minutes' WHERE id = $1",
                claimed["id"],
            )
            reclaimed = await reclaim_stale(conn, timeout_seconds=30)
            state = await conn.fetchrow(
                "SELECT status::text AS status, worker_id, last_heartbeat FROM jobs WHERE id = $1",
                claimed["id"],
            )

        assert any(item["id"] == claimed["id"] for item in reclaimed)
        assert state is not None
        assert state["status"] == "dead"
        assert state["worker_id"] is None
        assert state["last_heartbeat"] is None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_fail_retries_then_dead_letters_at_max_attempts() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    worker_id = uuid4()
    try:
        async with pool.acquire() as conn:
            enqueued = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 4},
                idempotency_key="integration:fail",
                max_attempts=2,
            )
            assert enqueued is not None

            first = await claim(conn, worker_id)
            assert first is not None
            assert first["id"] == enqueued["id"]
            assert int(first["attempts"]) == 1
            first_status = await fail(
                conn,
                first["id"],
                worker_id,
                attempts=int(first["attempts"]),
                error="first failure",
            )
            assert first_status == "pending"

            await conn.execute("UPDATE jobs SET run_at = NOW() WHERE id = $1", first["id"])
            second = await claim(conn, worker_id)
            assert second is not None
            assert second["id"] == enqueued["id"]
            assert int(second["attempts"]) == 2
            second_status = await fail(
                conn,
                second["id"],
                worker_id,
                attempts=int(second["attempts"]),
                error="second failure",
            )
            assert second_status == "dead"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reclaimed_summary_worker_cannot_reconcile_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None  # nosec B101
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=4)
    stale_worker = uuid4()
    replacement_worker = uuid4()
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()
    reconciled: list[dict] = []

    class _Trace:
        def finish(self, **kwargs) -> None:
            return None

    async def handler(payload):
        handler_started.set()
        await release_handler.wait()
        return {"validation": {"passed": True}}

    async def stalled_heartbeat(job_id):
        await asyncio.Event().wait()

    async def mark_started(conn, *, task_name, payload):
        return None

    async def record_reconcile(conn, *, payload, result):
        reconciled.append(dict(payload))

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: handler)
    monkeypatch.setattr(worker_module, "mark_job_started", mark_started)
    monkeypatch.setattr(worker_module, "reconcile_generation_result", record_reconcile)
    monkeypatch.setattr(worker_module, "start_summary_trace", lambda **kwargs: _Trace())

    try:
        async with pool.acquire() as conn:
            enqueued = await enqueue(
                conn,
                task_name="generate_process_summary",
                payload={
                    "process_id": str(uuid4()),
                    "version_id": str(uuid4()),
                },
                idempotency_key="integration:late-summary-fencing",
                max_attempts=3,
            )
            assert enqueued is not None  # nosec B101
            first = await claim(conn, stale_worker)
            assert first is not None  # nosec B101

        worker = Worker(
            pool,
            WorkerSettings(
                database_url=TEST_DATABASE_URL,
                concurrency=1,
                heartbeat_interval_seconds=1,
                stale_after_seconds=2,
                task_timeout_seconds=5,
                reclaim_interval_seconds=1,
            ),
            worker_id=stale_worker,
        )
        monkeypatch.setattr(worker, "_heartbeat_loop", stalled_heartbeat)
        running = asyncio.create_task(worker._run_job(first))
        await asyncio.wait_for(handler_started.wait(), timeout=1)

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET last_heartbeat = NOW() - interval '10 minutes' WHERE id = $1",
                first["id"],
            )
            reclaimed = await reclaim_stale(conn, timeout_seconds=30)
            assert any(item["id"] == first["id"] for item in reclaimed)  # nosec B101
            second = await claim(conn, replacement_worker)
            assert second is not None  # nosec B101
            assert second["id"] == first["id"]  # nosec B101

        release_handler.set()
        await asyncio.wait_for(running, timeout=2)

        async with pool.acquire() as conn:
            state = await conn.fetchrow(
                "SELECT status::text AS status, worker_id FROM jobs WHERE id = $1",
                first["id"],
            )

        assert reconciled == []  # nosec B101
        assert state is not None  # nosec B101
        assert state["status"] == "processing"  # nosec B101
        assert state["worker_id"] == replacement_worker  # nosec B101
    finally:
        release_handler.set()
        await pool.close()
