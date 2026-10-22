from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

MAX_JOB_ERROR_LOG_CHARS = 16_000
MIN_JOB_ATTEMPTS = 1
MAX_JOB_ATTEMPTS = 100

CLAIM_JOB_SQL = """
UPDATE jobs
SET status = 'processing',
    worker_id = $1,
    last_heartbeat = NOW(),
    attempts = attempts + 1,
    updated_at = NOW()
WHERE id = (
    SELECT id
    FROM jobs
    WHERE status = 'pending'
      AND run_at <= NOW()
    ORDER BY priority ASC, run_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, task_name, payload, attempts, max_attempts;
"""

GET_IDEMPOTENT_JOB_SQL = """
SELECT id, task_name, status::text AS status, payload
FROM jobs
WHERE idempotency_key = $1
FOR SHARE;
"""

ENQUEUE_SQL = """
INSERT INTO jobs (
    task_name, payload, priority, run_at, max_attempts, idempotency_key
)
VALUES ($1, $2::jsonb, $3, COALESCE($4, NOW()), $5, $6)
ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
DO NOTHING
RETURNING id, task_name, status, attempts, max_attempts;
"""

HEARTBEAT_SQL = """
UPDATE jobs
SET last_heartbeat = NOW(), updated_at = NOW()
WHERE id = $1 AND worker_id = $2 AND status = 'processing'
RETURNING id;
"""

COMPLETE_SQL = """
UPDATE jobs
SET status = 'completed', result = $3::jsonb, updated_at = NOW(), last_heartbeat = NOW()
WHERE id = $1 AND worker_id = $2 AND status = 'processing'
RETURNING id;
"""

FAIL_SQL = """
UPDATE jobs
SET status = CASE
        WHEN $5::boolean OR attempts >= max_attempts THEN 'dead'::job_status
        ELSE 'pending'::job_status
    END,
    run_at = CASE
        WHEN $5::boolean OR attempts >= max_attempts THEN run_at
        ELSE $3
    END,
    worker_id = NULL,
    last_heartbeat = NULL,
    error_log = RIGHT(COALESCE(error_log, '') || $4, $6),
    updated_at = NOW()
WHERE id = $1 AND worker_id = $2 AND status = 'processing'
RETURNING status;
"""

RECLAIM_SQL = """
WITH stale AS (
    SELECT id
    FROM jobs
    WHERE status = 'processing'
      AND last_heartbeat < NOW() - make_interval(secs => $1)
    FOR UPDATE SKIP LOCKED
)
UPDATE jobs j
SET status = CASE
        WHEN attempts >= max_attempts THEN 'dead'::job_status
        ELSE 'pending'::job_status
    END,
    worker_id = NULL,
    last_heartbeat = NULL,
    error_log = RIGHT(
        COALESCE(error_log, '') || E'\nWorker heartbeat timed out.',
        $2
    ),
    updated_at = NOW()
FROM stale
WHERE j.id = stale.id
RETURNING j.id, j.status, j.task_name, j.payload;
"""


def _strict_json_dumps(value: Any, *, label: str) -> str:
    try:
        return json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be strict JSON") from exc


def _retry_backoff_seconds(attempts: int) -> int:
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise ValueError("attempts must be an integer")
    exponent = min(9, max(1, attempts))
    return min(300, 2 ** exponent)

def _validate_reclaim_timeout(timeout_seconds: int) -> int:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise ValueError("reclaim timeout_seconds must be an integer")
    if timeout_seconds <= 0:
        raise ValueError("reclaim timeout_seconds must be greater than zero")
    return timeout_seconds


def _validate_max_attempts(max_attempts: int) -> int:
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise ValueError("max_attempts must be an integer")
    if not MIN_JOB_ATTEMPTS <= max_attempts <= MAX_JOB_ATTEMPTS:
        raise ValueError(
            f"max_attempts must be between {MIN_JOB_ATTEMPTS} and {MAX_JOB_ATTEMPTS}"
        )
    return max_attempts


async def enqueue(
    conn: asyncpg.Connection,
    *,
    task_name: str,
    payload: dict[str, Any],
    priority: int = 10,
    run_at: datetime | None = None,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
) -> asyncpg.Record | None:
    bounded_attempts = _validate_max_attempts(max_attempts)
    return await conn.fetchrow(
        ENQUEUE_SQL,
        task_name,
        _strict_json_dumps(payload, label="job payload"),
        priority,
        run_at,
        bounded_attempts,
        idempotency_key,
    )


async def get_idempotent_job(
    conn: asyncpg.Connection, idempotency_key: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(GET_IDEMPOTENT_JOB_SQL, idempotency_key)


async def claim(conn: asyncpg.Connection, worker_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(CLAIM_JOB_SQL, worker_id)


async def heartbeat(conn: asyncpg.Connection, job_id: UUID, worker_id: UUID) -> bool:
    return await conn.fetchrow(HEARTBEAT_SQL, job_id, worker_id) is not None


async def complete(
    conn: asyncpg.Connection,
    job_id: UUID,
    worker_id: UUID,
    result: dict[str, Any] | None = None,
) -> bool:
    return await conn.fetchrow(
        COMPLETE_SQL,
        job_id,
        worker_id,
        _strict_json_dumps(result or {}, label="job result"),
    ) is not None


async def fail(
    conn: asyncpg.Connection,
    job_id: UUID,
    worker_id: UUID,
    *,
    attempts: int,
    error: str,
    permanent: bool = False,
) -> str | None:
    backoff_seconds = _retry_backoff_seconds(attempts)
    retry_at = datetime.now(UTC) + timedelta(seconds=backoff_seconds)
    row = await conn.fetchrow(
        FAIL_SQL,
        job_id,
        worker_id,
        retry_at,
        f"\n[{datetime.now(UTC).isoformat()}] {error}",
        permanent,
        MAX_JOB_ERROR_LOG_CHARS,
    )
    return str(row["status"]) if row else None


async def reclaim_stale(conn: asyncpg.Connection, timeout_seconds: int = 30) -> list[asyncpg.Record]:
    bounded_timeout = _validate_reclaim_timeout(timeout_seconds)
    return list(
        await conn.fetch(RECLAIM_SQL, bounded_timeout, MAX_JOB_ERROR_LOG_CHARS)
    )
