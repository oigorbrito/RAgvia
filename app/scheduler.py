from __future__ import annotations

import asyncio
import logging
import math
import os
from contextlib import suppress
from datetime import timedelta

import asyncpg

from app.db import create_pool
from app.tracking_scheduler import enqueue_due_tracking_reconciliations

logger = logging.getLogger(__name__)
LOCK_NAME = "rpy_scheduler"


def _positive(value: int | float, *, name: str) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


async def expurgar(conn: asyncpg.Connection, *, retention_days: int) -> int:
    _positive(retention_days, name="retention_days")
    deleted = await conn.fetchval(
        """
        WITH doomed AS (
            SELECT id
            FROM processes
            WHERE updated_at < NOW() - make_interval(days => $1)
        ),
        request_ids AS (
            SELECT DISTINCT judit_request_id
            FROM process_versions
            WHERE process_id IN (SELECT id FROM doomed)
              AND judit_request_id IS NOT NULL
        ),
        deleted_deliveries AS (
            DELETE FROM judit_deliveries
            WHERE request_id IN (SELECT judit_request_id FROM request_ids)
            RETURNING callback_id
        ),
        deleted_processes AS (
            DELETE FROM processes
            WHERE id IN (SELECT id FROM doomed)
            RETURNING id
        )
        SELECT count(*) FROM deleted_processes
        """,
        retention_days,
    )
    return int(deleted or 0)


async def purge_terminal_jobs(conn: asyncpg.Connection, *, retention_days: int) -> int:
    """Delete only completed/dead jobs after the operational retention window."""
    _positive(retention_days, name="job_retention_days")
    deleted = await conn.fetchval(
        """
        WITH deleted_jobs AS (
            DELETE FROM jobs
            WHERE status IN ('completed', 'dead')
              AND updated_at < NOW() - make_interval(days => $1)
            RETURNING id
        )
        SELECT count(*) FROM deleted_jobs
        """,
        retention_days,
    )
    return int(deleted or 0)


async def purge_judit_request_completions(
    conn: asyncpg.Connection,
    *,
    retention_days: int,
) -> int:
    """Bound completion markers to the same operational window as terminal jobs."""
    _positive(retention_days, name="job_retention_days")
    deleted = await conn.fetchval(
        """
        WITH deleted_markers AS (
            DELETE FROM judit_request_completions
            WHERE completed_at < NOW() - make_interval(days => $1)
            RETURNING request_id
        )
        SELECT count(*) FROM deleted_markers
        """,
        retention_days,
    )
    return int(deleted or 0)


async def purge_judit_deliveries(
    conn: asyncpg.Connection,
    *,
    retention_days: int,
) -> int:
    """Bound raw Judit webhook payload retention to the operational dedupe window."""
    _positive(retention_days, name="job_retention_days")
    deleted = await conn.fetchval(
        """
        WITH deleted_deliveries AS (
            DELETE FROM judit_deliveries
            WHERE received_at < NOW() - make_interval(days => $1)
            RETURNING callback_id
        )
        SELECT count(*) FROM deleted_deliveries
        """,
        retention_days,
    )
    return int(deleted or 0)


async def acquire_singleton(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_NAME))


async def release_singleton(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", LOCK_NAME)


async def run_scheduler() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    retention_days = int(_positive(int(os.getenv("RETENTION_DAYS", "365")), name="RETENTION_DAYS"))
    job_retention_days = int(
        _positive(int(os.getenv("JOB_RETENTION_DAYS", "30")), name="JOB_RETENTION_DAYS")
    )
    interval_seconds = float(
        _positive(
            float(
                os.getenv(
                    "EXPUNGE_INTERVAL_SECONDS",
                    str(timedelta(hours=24).total_seconds()),
                )
            ),
            name="EXPUNGE_INTERVAL_SECONDS",
        )
    )
    tracking_stale_hours = float(
        _positive(
            float(os.getenv("TRACKING_STALE_HOURS", "36")),
            name="TRACKING_STALE_HOURS",
        )
    )
    tracking_reconcile_batch = int(
        _positive(
            int(os.getenv("TRACKING_RECONCILE_BATCH", "25")),
            name="TRACKING_RECONCILE_BATCH",
        )
    )

    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as lock_conn:
            if not await acquire_singleton(lock_conn):
                raise RuntimeError(
                    "another Rpy scheduler instance already holds the singleton lock"
                )
            try:
                while True:
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            tracking_reconciliations = await enqueue_due_tracking_reconciliations(
                                conn,
                                stale_after=timedelta(hours=tracking_stale_hours),
                                limit=tracking_reconcile_batch,
                            )
                        deleted_processes = await expurgar(
                            conn, retention_days=retention_days
                        )
                        deleted_jobs = await purge_terminal_jobs(
                            conn, retention_days=job_retention_days
                        )
                        deleted_completion_markers = await purge_judit_request_completions(
                            conn,
                            retention_days=job_retention_days,
                        )
                        deleted_deliveries = await purge_judit_deliveries(
                            conn,
                            retention_days=job_retention_days,
                        )
                    logger.info(
                        "maintenance completed: %s tracking reconciliation(s) enqueued, %s process(es) expunged, %s terminal job(s) purged, %s Judit completion marker(s) purged, %s Judit delivery payload(s) purged",
                        tracking_reconciliations,
                        deleted_processes,
                        deleted_jobs,
                        deleted_completion_markers,
                        deleted_deliveries,
                    )
                    await asyncio.sleep(interval_seconds)
            finally:
                with suppress(Exception):
                    await release_singleton(lock_conn)
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(run_scheduler())
