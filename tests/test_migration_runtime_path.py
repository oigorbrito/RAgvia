from __future__ import annotations

from pathlib import Path

import pytest

from app.migrations import MIGRATIONS_DIR_ENV, resolve_migrations


def test_resolve_migrations_uses_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    first = sql_dir / "001_init.sql"
    second = sql_dir / "002_more.sql"
    second.write_text("SELECT 2;", encoding="utf-8")
    first.write_text("SELECT 1;", encoding="utf-8")

    monkeypatch.setenv(MIGRATIONS_DIR_ENV, str(sql_dir))

    directory, paths = resolve_migrations()

    assert directory == sql_dir
    assert [path.name for path in paths] == ["001_init.sql", "002_more.sql"]


def test_resolve_migrations_fails_closed_for_empty_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(MIGRATIONS_DIR_ENV, str(tmp_path))

    with pytest.raises(RuntimeError, match="contains no SQL files"):
        resolve_migrations()


def test_explicit_migration_directory_overrides_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured"
    configured.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (configured / "001_wrong.sql").write_text("SELECT 0;", encoding="utf-8")
    (explicit / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setenv(MIGRATIONS_DIR_ENV, str(configured))

    directory, paths = resolve_migrations(explicit)

    assert directory == explicit
    assert [path.name for path in paths] == ["001_init.sql"]
