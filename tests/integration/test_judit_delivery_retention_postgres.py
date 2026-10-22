from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.scheduler import purge_judit_deliveries

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_judit_delivery_retention_deletes_only_old_payloads() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    old_callback = f"old-{uuid4()}"
    recent_callback = f"recent-{uuid4()}"
    try:
        await conn.execute(
            """
            INSERT INTO judit_deliveries (
                callback_id, request_id, event_type, raw_payload, received_at
            ) VALUES
                ($1, 'req-old', 'request_completed', $2::jsonb, $3),
                ($4, 'req-recent', 'request_completed', $5::jsonb, $6)
            """,
            old_callback,
            json.dumps({"sensitive": "old raw webhook payload"}),
            datetime.now(UTC) - timedelta(days=60),
            recent_callback,
            json.dumps({"sensitive": "recent raw webhook payload"}),
            datetime.now(UTC) - timedelta(days=2),
        )

        deleted = await purge_judit_deliveries(conn, retention_days=30)
        assert deleted >= 1

        old_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM judit_deliveries WHERE callback_id = $1)",
            old_callback,
        )
        recent_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM judit_deliveries WHERE callback_id = $1)",
            recent_callback,
        )
        assert old_exists is False
        assert recent_exists is True
    finally:
        await conn.execute(
            "DELETE FROM judit_deliveries WHERE callback_id = ANY($1::text[])",
            [old_callback, recent_callback],
        )
        await conn.close()


@pytest.mark.asyncio
async def test_judit_delivery_retention_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="job_retention_days"):
        await purge_judit_deliveries(None, retention_days=0)  # type: ignore[arg-type]
