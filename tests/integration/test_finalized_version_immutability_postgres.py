from __future__ import annotations

import os
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
async def test_finalized_version_source_payload_is_immutable_on_restaging_and_direct_sql() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    code = _code()
    source_id = f"response-{uuid4()}"
    request_id = f"request-{uuid4()}"
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

        # Before finalization, a retry may legitimately refresh staging data.
        _, same_version = await stage_version(
            conn,
            code=code,
            source_request_id=source_id,
            cached_response=True,
            payload={"source": "pre-finalize-refresh"},
            judit_request_id=request_id,
            judit_response_id=source_id,
            judit_callback_id="callback-refresh",
        )
        assert same_version == version_id

        await finalize_version(
            conn,
            process_id=process_id,
            version_id=version_id,
            header={},
            parties=[],
            subjects=[],
            steps=[],
        )

        before = await conn.fetchrow(
            """
            SELECT source_cached_response, source_payload, judit_request_id,
                   judit_response_id, judit_callback_id
            FROM process_versions
            WHERE id = $1
            """,
            version_id,
        )
        assert before is not None

        _, retried_version = await stage_version(
            conn,
            code=code,
            source_request_id=source_id,
            cached_response=False,
            payload={"source": "mutated-after-finalize"},
            judit_request_id="different-request",
            judit_response_id="different-response",
            judit_callback_id="different-callback",
        )
        assert retried_version == version_id

        after = await conn.fetchrow(
            """
            SELECT source_cached_response, source_payload, judit_request_id,
                   judit_response_id, judit_callback_id
            FROM process_versions
            WHERE id = $1
            """,
            version_id,
        )
        assert dict(after) == dict(before)

        with pytest.raises(asyncpg.PostgresError, match="source data is immutable"):
            await conn.execute(
                "UPDATE process_versions SET source_payload = $2::jsonb WHERE id = $1",
                version_id,
                '{"source":"direct-sql-mutation"}',
            )
    finally:
        await conn.execute("DELETE FROM processes WHERE code = $1", code)
        await conn.close()
