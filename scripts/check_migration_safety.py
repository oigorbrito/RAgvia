from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
DOCS_DIR = ROOT / "docs" / "migrations"
WAIVER_MARKER = "-- migration-safety: allow-destructive"
WAIVER_HEADING = "## Destructive migration approval"

DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DROP object", re.compile(r"\bdrop\s+(?:table|column|type|schema|extension)\b", re.I)),
    ("TRUNCATE", re.compile(r"\btruncate\b", re.I)),
    (
        "ALTER COLUMN TYPE",
        re.compile(r"\balter\s+table\b[\s\S]*?\balter\s+column\b[\s\S]*?\btype\b", re.I),
    ),
    (
        "SET NOT NULL",
        re.compile(r"\balter\s+table\b[\s\S]*?\balter\s+column\b[\s\S]*?\bset\s+not\s+null\b", re.I),
    ),
    (
        "RENAME",
        re.compile(r"\balter\s+(?:table|type)\b[\s\S]*?\brename\s+(?:column\s+|to\s+)", re.I),
    ),
)


def _without_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def dangerous_operations(sql: str) -> list[str]:
    cleaned = _without_comments(sql)
    return [label for label, pattern in DANGEROUS_PATTERNS if pattern.search(cleaned)]


def migration_violations(path: Path) -> list[str]:
    sql = path.read_text(encoding="utf-8")
    operations = dangerous_operations(sql)
    has_marker = WAIVER_MARKER in sql
    doc_path = DOCS_DIR / f"{path.stem}.md"
    documented = doc_path.exists() and WAIVER_HEADING in doc_path.read_text(
        encoding="utf-8", errors="ignore"
    )

    if not operations:
        return ["stale destructive-migration waiver marker"] if has_marker else []
    if has_marker and documented:
        return []

    detail = ", ".join(operations)
    return [
        f"potentially destructive SQL ({detail}); use expand/migrate/contract instead. "
        f"If destruction is intentional, add '{WAIVER_MARKER}' and document "
        f"'{WAIVER_HEADING}' in docs/migrations/{path.stem}.md"
    ]


def main() -> int:
    violations: list[tuple[str, str]] = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        for message in migration_violations(path):
            violations.append((str(path.relative_to(ROOT)), message))

    if violations:
        print("Migration safety guard: FAILED")
        for path, message in violations:
            print(f" - {path}: {message}")
        return 1

    print("Migration safety guard: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
