from __future__ import annotations

import os

import asyncpg
import pytest

from app.migrations import migrate
from app.queue import enqueue

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_enqueue_accepts_upper_supported_attempt_bound() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
        row = await enqueue(
            conn,
            task_name="healthcheck",
            payload={},
            max_attempts=100,
            idempotency_key="integration:max-attempts-upper-bound",
        )
        assert row is not None
        assert int(row["max_attempts"]) == 100
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_attempts", [0, 101])
async def test_database_rejects_direct_invalid_attempt_count(invalid_attempts: int) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO jobs (task_name, payload, max_attempts) VALUES ('healthcheck', '{}'::jsonb, $1)",
                invalid_attempts,
            )
    finally:
        await conn.close()
