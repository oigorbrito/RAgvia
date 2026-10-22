from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from app.artifact_provenance import sha256_file
from app.tpu_glossary import TPU_SCHEMA_VERSION

DEFAULT_PUBLISHER = "Conselho Nacional de Justiça (CNJ)"
DEFAULT_SOURCE = "Sistema de Gestão de Tabelas Processuais Unificadas"
REQUIRED_COLUMNS = ("kind", "code", "name", "definition", "source_ref")
ALLOWED_KINDS = {"class", "subject"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _validate_version(value: str) -> str:
    rendered = _clean(value)
    parts = rendered.split("-")
    if (
        len(parts) != 3
        or any(not part.isdigit() for part in parts)
        or len(parts[0]) != 4
        or len(parts[1]) != 2
        or len(parts[2]) != 2
    ):
        raise ValueError("tpu_version must use YYYY-MM-DD")
    return rendered


def load_normalized_csv(path: Path) -> list[dict[str, str]]:
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError as exc:
        raise ValueError(f"input CSV not found: {path}") from exc

    with handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing = [name for name in REQUIRED_COLUMNS if name not in fieldnames]
        if missing:
            raise ValueError(
                "input CSV is missing required columns: " + ", ".join(missing)
            )

        entries: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row_number, row in enumerate(reader, start=2):
            kind = _clean(row.get("kind")).lower()
            code = _clean(row.get("code"))
            name = _clean(row.get("name"))
            definition = _clean(row.get("definition"))
            source_ref = _clean(row.get("source_ref"))

            if kind not in ALLOWED_KINDS:
                raise ValueError(f"row {row_number}: invalid kind {kind!r}")
            if not code:
                raise ValueError(f"row {row_number}: code is required")
            if not code.isdigit():
                raise ValueError(f"row {row_number}: code must contain digits only")
            if not name:
                raise ValueError(f"row {row_number}: name is required")
            if not definition:
                raise ValueError(f"row {row_number}: definition is required")
            if not source_ref:
                raise ValueError(f"row {row_number}: source_ref is required")

            key = (kind, code)
            if key in seen:
                raise ValueError(
                    f"row {row_number}: duplicate TPU entry {kind}:{code}"
                )
            seen.add(key)
            entries.append(
                {
                    "kind": kind,
                    "code": code,
                    "name": name,
                    "definition": definition,
                    "source_ref": source_ref,
                }
            )

    entries.sort(
        key=lambda entry: (
            0 if entry["kind"] == "class" else 1,
            int(entry["code"]),
            entry["code"],
        )
    )
    return entries


def build_snapshot(
    *,
    entries: list[dict[str, str]],
    tpu_version: str,
    source_version_label: str,
    publisher: str = DEFAULT_PUBLISHER,
    source: str = DEFAULT_SOURCE,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    version = _validate_version(tpu_version)
    label = _clean(source_version_label)
    if not label:
        raise ValueError("source_version_label is required")
    publisher_value = _clean(publisher)
    source_value = _clean(source)
    if not publisher_value or not source_value:
        raise ValueError("publisher and source are required")

    snapshot: dict[str, Any] = {
        "schema_version": TPU_SCHEMA_VERSION,
        "tpu_version": version,
        "publisher": publisher_value,
        "source": source_value,
        "source_version_label": label,
        "entries": entries,
    }
    if input_sha256:
        snapshot["build_metadata"] = {"input_sha256": input_sha256}
    return snapshot


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    if path.exists():
        raise ValueError(
            f"output already exists: {path}; create a new versioned snapshot instead"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic Rpy TPU snapshot from a locally obtained, "
            "normalized CNJ export without network access."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="normalized CSV")
    parser.add_argument("--output", required=True, type=Path, help="new JSON snapshot")
    parser.add_argument("--tpu-version", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--source-version-label",
        required=True,
        help="source release label exactly as published by CNJ",
    )
    parser.add_argument("--publisher", default=DEFAULT_PUBLISHER)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        entries = load_normalized_csv(args.input)
        snapshot = build_snapshot(
            entries=entries,
            tpu_version=args.tpu_version,
            source_version_label=args.source_version_label,
            publisher=args.publisher,
            source=args.source,
            input_sha256=sha256_file(args.input),
        )
        write_snapshot(args.output, snapshot)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"TPU snapshot written: {args.output} "
        f"({len(snapshot['entries'])} entries, version {snapshot['tpu_version']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
