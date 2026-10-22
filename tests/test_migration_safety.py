from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_migration_safety.py"
spec = importlib.util.spec_from_file_location("check_migration_safety", MODULE_PATH)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_additive_sql_is_allowed(tmp_path: Path, monkeypatch) -> None:
    sql_dir = tmp_path / "sql"
    docs_dir = tmp_path / "docs" / "migrations"
    sql_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    path = sql_dir / "001_add.sql"
    path.write_text("ALTER TABLE example ADD COLUMN IF NOT EXISTS note TEXT;", encoding="utf-8")
    monkeypatch.setattr(guard, "DOCS_DIR", docs_dir)
    assert guard.migration_violations(path) == []


def test_drop_requires_explicit_documented_waiver(tmp_path: Path, monkeypatch) -> None:
    sql_dir = tmp_path / "sql"
    docs_dir = tmp_path / "docs" / "migrations"
    sql_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    path = sql_dir / "002_drop.sql"
    path.write_text("DROP TABLE legacy;", encoding="utf-8")
    monkeypatch.setattr(guard, "DOCS_DIR", docs_dir)
    violations = guard.migration_violations(path)
    assert len(violations) == 1
    assert "potentially destructive SQL" in violations[0]


def test_documented_waiver_allows_intentional_destructive_change(tmp_path: Path, monkeypatch) -> None:
    sql_dir = tmp_path / "sql"
    docs_dir = tmp_path / "docs" / "migrations"
    sql_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    path = sql_dir / "003_contract.sql"
    path.write_text(
        f"{guard.WAIVER_MARKER}\nALTER TABLE example DROP COLUMN old_value;",
        encoding="utf-8",
    )
    (docs_dir / "003_contract.md").write_text(
        f"# Contract migration\n\n{guard.WAIVER_HEADING}\n\nApproved after compatibility window.",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "DOCS_DIR", docs_dir)
    assert guard.migration_violations(path) == []


def test_stale_waiver_marker_is_rejected(tmp_path: Path, monkeypatch) -> None:
    sql_dir = tmp_path / "sql"
    docs_dir = tmp_path / "docs" / "migrations"
    sql_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    path = sql_dir / "004_safe.sql"
    path.write_text(f"{guard.WAIVER_MARKER}\nCREATE TABLE safe(id integer);", encoding="utf-8")
    monkeypatch.setattr(guard, "DOCS_DIR", docs_dir)
    assert guard.migration_violations(path) == ["stale destructive-migration waiver marker"]


def test_repository_migrations_are_safe() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(guard.SQL_DIR.glob("*.sql")):
        for message in guard.migration_violations(path):
            violations.append((path.name, message))
    assert violations == []
