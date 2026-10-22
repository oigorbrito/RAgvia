from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import MIGRATIONS_DIR, MigrationDriftError, migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_default_migrations_are_recorded_and_repeatable() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    await migrate(TEST_DATABASE_URL)

    expected = {path.name for path in MIGRATIONS_DIR.glob("*.sql")}
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT filename, checksum_sha256 FROM schema_migrations WHERE filename = ANY($1::text[])",
            list(expected),
        )
    finally:
        await conn.close()

    assert {row["filename"] for row in rows} == expected
    assert all(len(str(row["checksum_sha256"])) == 64 for row in rows)


@pytest.mark.asyncio
async def test_applied_migration_checksum_drift_is_rejected(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL is not None
    suffix = uuid4().hex[:8]
    filename = f"900_drift_{suffix}.sql"
    table = f"migration_drift_{suffix}"
    path = tmp_path / filename
    path.write_text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY);", encoding="utf-8")

    await migrate(TEST_DATABASE_URL, migrations_dir=tmp_path)
    path.write_text(
        f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, changed TEXT);",
        encoding="utf-8",
    )

    with pytest.raises(MigrationDriftError, match="changed after being applied"):
        await migrate(TEST_DATABASE_URL, migrations_dir=tmp_path)

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await conn.execute("DELETE FROM schema_migrations WHERE filename = $1", filename)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_advisory_lock_serializes_competing_runners(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL is not None
    suffix = uuid4().hex[:8]
    filename = f"901_lock_{suffix}.sql"
    table = f"migration_lock_{suffix}"
    (tmp_path / filename).write_text(
        f"SELECT pg_sleep(0.2); CREATE TABLE {table} (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    await asyncio.gather(
        migrate(TEST_DATABASE_URL, migrations_dir=tmp_path),
        migrate(TEST_DATABASE_URL, migrations_dir=tmp_path),
    )

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename = $1",
            filename,
        ) == 1
        assert await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL",
            table,
        ) is True
        await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await conn.execute("DELETE FROM schema_migrations WHERE filename = $1", filename)
    finally:
        await conn.close()
