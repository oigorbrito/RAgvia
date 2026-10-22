from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.scheduler import purge_judit_request_completions

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_completion_marker_retention_deletes_only_old_rows() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    old_request = f"old-{uuid4()}"
    recent_request = f"recent-{uuid4()}"
    try:
        await conn.execute(
            "INSERT INTO judit_request_completions (request_id, completed_at) VALUES ($1, $2), ($3, $4)",
            old_request,
            datetime.now(UTC) - timedelta(days=60),
            recent_request,
            datetime.now(UTC) - timedelta(days=2),
        )

        deleted = await purge_judit_request_completions(conn, retention_days=30)
        assert deleted >= 1

        old_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM judit_request_completions WHERE request_id = $1)",
            old_request,
        )
        recent_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM judit_request_completions WHERE request_id = $1)",
            recent_request,
        )
        assert old_exists is False
        assert recent_exists is True
    finally:
        await conn.execute(
            "DELETE FROM judit_request_completions WHERE request_id = ANY($1::text[])",
            [old_request, recent_request],
        )
        await conn.close()


@pytest.mark.asyncio
async def test_completion_marker_retention_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="job_retention_days"):
        await purge_judit_request_completions(None, retention_days=0)  # type: ignore[arg-type]
