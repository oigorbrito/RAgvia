from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.processes import preferred_judit_version, stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_preferred_version_accepts_callback_key_when_response_id_is_missing() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    request_id = f"req-{uuid4()}"
    callback_id = f"cb-{uuid4()}"
    code = "0000000-00.2026.8.21.0888"
    try:
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=callback_id,
            cached_response=False,
            payload={
                "callback_id": callback_id,
                "event_type": "response_created",
                "reference_type": "request",
                "reference_id": request_id,
                "payload": {
                    "request_id": request_id,
                    "response_type": "lawsuit",
                    "response_data": {"code": code, "steps": []},
                },
            },
            judit_request_id=request_id,
            judit_response_id=None,
            judit_callback_id=callback_id,
        )

        preferred = await preferred_judit_version(conn, request_id=request_id)
        assert preferred is not None
        assert preferred["process_id"] == process_id
        assert preferred["version_id"] == version_id
    finally:
        await conn.execute("DELETE FROM processes WHERE code = $1", code)
        await conn.close()
