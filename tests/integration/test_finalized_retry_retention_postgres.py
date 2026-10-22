from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.processes import finalize_version, stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _code() -> str:
    suffix = uuid4().int % 10_000
    return f"0000000-00.2026.8.21.{suffix:04d}"


@pytest.mark.asyncio
async def test_duplicate_finalized_stage_does_not_refresh_process_retention_clock() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    code = _code()
    request_id = f"request-{uuid4()}"
    source_id = f"response-{uuid4()}"
    try:
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=source_id,
            cached_response=False,
            payload={"source": "original"},
            judit_request_id=request_id,
            judit_response_id=source_id,
            judit_callback_id="callback-original",
        )
        await finalize_version(
            conn,
            process_id=process_id,
            version_id=version_id,
            header={},
            parties=[],
            subjects=[],
            steps=[],
        )

        old_activity = datetime.now(UTC) - timedelta(days=400)
        await conn.execute(
            "UPDATE processes SET updated_at = $2 WHERE id = $1",
            process_id,
            old_activity,
        )

        await stage_version(
            conn,
            code=code,
            source_request_id=source_id,
            cached_response=False,
            payload={"source": "duplicate-finalized"},
            judit_request_id=request_id,
            judit_response_id=source_id,
            judit_callback_id="callback-duplicate",
        )
        unchanged_activity = await conn.fetchval(
            "SELECT updated_at FROM processes WHERE id = $1",
            process_id,
        )
        assert unchanged_activity == old_activity

        await stage_version(
            conn,
            code=code,
            source_request_id=f"response-new-{uuid4()}",
            cached_response=False,
            payload={"source": "new-version"},
            judit_request_id=f"request-new-{uuid4()}",
            judit_response_id=f"response-new-id-{uuid4()}",
            judit_callback_id="callback-new",
        )
        refreshed_activity = await conn.fetchval(
            "SELECT updated_at FROM processes WHERE id = $1",
            process_id,
        )
        assert refreshed_activity > old_activity
    finally:
        await conn.execute("DELETE FROM processes WHERE code = $1", code)
        await conn.close()
