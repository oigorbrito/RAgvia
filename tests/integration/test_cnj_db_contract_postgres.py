from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _canonical_code() -> str:
    prefix = uuid4().int % 10_000_000
    suffix = uuid4().int % 10_000
    return f"{prefix:07d}-00.2026.8.21.{suffix:04d}"


@pytest.mark.asyncio
async def test_process_code_constraint_accepts_canonical_and_rejects_noncanonical() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    canonical = _canonical_code()
    digits_only = "".join(character for character in canonical if character.isdigit())
    try:
        inserted = await conn.fetchval(
            "INSERT INTO processes (code) VALUES ($1) RETURNING code",
            canonical,
        )
        assert inserted == canonical

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO processes (code) VALUES ($1)",
                digits_only,
            )
    finally:
        await conn.execute("DELETE FROM processes WHERE code = $1", canonical)
        await conn.close()
