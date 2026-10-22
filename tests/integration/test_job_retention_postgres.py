from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.scheduler import purge_terminal_jobs

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_job_retention_deletes_only_old_completed_and_dead_jobs() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        marker = f"retention-{uuid4()}"
        old_time = datetime.now(UTC) - timedelta(days=60)
        recent_time = datetime.now(UTC) - timedelta(days=2)

        rows = [
            (f"{marker}-completed-old", "completed", old_time),
            (f"{marker}-dead-old", "dead", old_time),
            (f"{marker}-pending-old", "pending", old_time),
            (f"{marker}-processing-old", "processing", old_time),
            (f"{marker}-completed-recent", "completed", recent_time),
        ]
        for key, status, updated_at in rows:
            await conn.execute(
                """
                INSERT INTO jobs (
                    task_name, payload, status, result, error_log,
                    idempotency_key, created_at, updated_at
                ) VALUES ('retention-test', $1::jsonb, $2::job_status, $3::jsonb, $4, $5, $6, $6)
                """,
                json.dumps({"marker": marker, "sensitive": "old queue payload"}),
                status,
                json.dumps({"marker": marker}),
                "terminal error details" if status == "dead" else None,
                key,
                updated_at,
            )

        deleted = await purge_terminal_jobs(conn, retention_days=30)
        assert deleted >= 2

        remaining = {
            row["idempotency_key"]: str(row["status"])
            for row in await conn.fetch(
                """
                SELECT idempotency_key, status
                FROM jobs
                WHERE idempotency_key LIKE $1
                """,
                f"{marker}%",
            )
        }
        assert f"{marker}-completed-old" not in remaining
        assert f"{marker}-dead-old" not in remaining
        assert remaining[f"{marker}-pending-old"] == "pending"
        assert remaining[f"{marker}-processing-old"] == "processing"
        assert remaining[f"{marker}-completed-recent"] == "completed"
    finally:
        await conn.execute("DELETE FROM jobs WHERE idempotency_key LIKE $1", f"{marker}%")
        await conn.close()


@pytest.mark.asyncio
async def test_job_retention_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="job_retention_days"):
        await purge_terminal_jobs(None, retention_days=0)  # type: ignore[arg-type]
