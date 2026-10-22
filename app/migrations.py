from __future__ import annotations

import hashlib
import os
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "sql"
MIGRATIONS_DIR_ENV = "RPY_MIGRATIONS_DIR"
MIGRATION_LOCK_KEY = 0x525059  # ASCII-ish stable key for "RPY".

CREATE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    checksum_sha256 TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class MigrationDriftError(RuntimeError):
    pass


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def resolve_migrations(
    migrations_dir: Path | None = None,
) -> tuple[Path, list[Path]]:
    if migrations_dir is not None:
        directory = migrations_dir
    else:
        configured = os.getenv(MIGRATIONS_DIR_ENV)
        directory = Path(configured) if configured else MIGRATIONS_DIR

    if not directory.is_dir():
        raise RuntimeError(f"migration directory does not exist: {directory}")

    paths = sorted(directory.glob("*.sql"))
    if not paths:
        raise RuntimeError(f"migration directory contains no SQL files: {directory}")
    return directory, paths


async def migrate(database_url: str, *, migrations_dir: Path | None = None) -> None:
    directory, paths = resolve_migrations(migrations_dir)
    conn = await asyncpg.connect(database_url)
    locked = False
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_KEY)
        locked = True
        await conn.execute(CREATE_LEDGER_SQL)

        for path in paths:
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)
            recorded = await conn.fetchval(
                "SELECT checksum_sha256 FROM schema_migrations WHERE filename = $1",
                path.name,
            )
            if recorded is not None:
                if str(recorded) != checksum:
                    raise MigrationDriftError(
                        f"migration {path.name} changed after being applied"
                    )
                continue

            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (filename, checksum_sha256)
                    VALUES ($1, $2)
                    """,
                    path.name,
                    checksum,
                )
            print(f"applied {path.name}")
    finally:
        if locked:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_KEY)
        await conn.close()
