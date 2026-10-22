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
    return f"0000000-00.2026.8.21.{uuid4().int % 10_000:04d}"


@pytest.mark.asyncio
async def test_refinalizing_current_version_is_a_noop() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    code = _code()
    try:
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=f"response-{uuid4()}",
            cached_response=False,
            payload={"source": "original"},
            judit_request_id=f"request-{uuid4()}",
            judit_response_id=f"response-id-{uuid4()}",
            judit_callback_id=f"callback-{uuid4()}",
        )
        first_steps = [
            {
                "step_number": 1,
                "occurred_at": None,
                "title": "ORIGINAL",
                "text": "original step",
                "metadata": {},
            }
        ]
        assert await finalize_version(
            conn,
            process_id=process_id,
            version_id=version_id,
            header={"marker": "original"},
            parties=[],
            subjects=[],
            steps=first_steps,
            court="TJRS",
            class_name="Original",
        ) is True

        old_activity = datetime.now(UTC) - timedelta(days=400)
        await conn.execute(
            "UPDATE processes SET updated_at = $2 WHERE id = $1",
            process_id,
            old_activity,
        )
        before = await conn.fetchrow(
            """
            SELECT p.updated_at, p.header, p.class_name, pv.finalized_at,
                   ps.title, ps.text
            FROM processes p
            JOIN process_versions pv ON pv.id = p.current_version_id
            JOIN process_steps ps ON ps.version_id = pv.id AND ps.step_number = 1
            WHERE p.id = $1
            """,
            process_id,
        )
        assert before is not None

        assert await finalize_version(
            conn,
            process_id=process_id,
            version_id=version_id,
            header={"marker": "mutated"},
            parties=[{"name": "Should Not Persist"}],
            subjects=[{"name": "Should Not Persist"}],
            steps=[
                {
                    "step_number": 1,
                    "occurred_at": None,
                    "title": "MUTATED",
                    "text": "mutated step",
                    "metadata": {},
                }
            ],
            court="OTHER",
            class_name="Mutated",
        ) is True

        after = await conn.fetchrow(
            """
            SELECT p.updated_at, p.header, p.class_name, pv.finalized_at,
                   ps.title, ps.text
            FROM processes p
            JOIN process_versions pv ON pv.id = p.current_version_id
            JOIN process_steps ps ON ps.version_id = pv.id AND ps.step_number = 1
            WHERE p.id = $1
            """,
            process_id,
        )
        assert dict(after) == dict(before)
        assert after["updated_at"] == old_activity
    finally:
        await conn.execute("DELETE FROM processes WHERE code = $1", code)
        await conn.close()
