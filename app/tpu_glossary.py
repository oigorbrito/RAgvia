from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Sequence

TPU_GLOSSARY_PATH_ENV = "TPU_GLOSSARY_PATH"
TPU_SCHEMA_VERSION = 1
TPU_SNAPSHOT_RELATIVE_PATH = Path("data") / "tpu" / "2026-09-12.json"

TPUKind = Literal["class", "subject"]


@dataclass(frozen=True, slots=True)
class TPUDefinition:
    kind: TPUKind
    code: str
    name: str
    definition: str
    tpu_version: str
    publisher: str
    source: str
    source_ref: str

    @property
    def definition_sha256(self) -> str:
        return hashlib.sha256(self.definition.encode("utf-8")).hexdigest()

    def as_context(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "code": self.code,
            "name": self.name,
            "definition": self.definition,
            "tpu_version": self.tpu_version,
            "publisher": self.publisher,
            "source": self.source,
            "source_ref": self.source_ref,
            "definition_sha256": self.definition_sha256,
        }

    def as_provenance(self, *, source_order: int) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "code": self.code,
            "tpu_version": self.tpu_version,
            "publisher": self.publisher,
            "source": self.source,
            "source_ref": self.source_ref,
            "definition_sha256": self.definition_sha256,
            "source_order": source_order,
        }


@dataclass(frozen=True, slots=True)
class TPUCatalog:
    tpu_version: str
    publisher: str
    source: str
    source_version_label: str
    entries: dict[tuple[TPUKind, str], TPUDefinition]

    def resolve(self, *, kind: TPUKind, code: str | int | None) -> TPUDefinition | None:
        if code is None:
            return None
        normalized = str(code).strip()
        if not normalized:
            return None
        return self.entries.get((kind, normalized))


def _default_catalog_path() -> Path:
    runtime_path = Path.cwd() / TPU_SNAPSHOT_RELATIVE_PATH
    if runtime_path.is_file():
        return runtime_path
    source_path = Path(__file__).resolve().parent.parent / TPU_SNAPSHOT_RELATIVE_PATH
    if source_path.is_file():
        return source_path
    return runtime_path


def _catalog_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = str(os.environ.get(TPU_GLOSSARY_PATH_ENV) or "").strip()
    if configured:
        return Path(configured)
    return _default_catalog_path()


def load_tpu_catalog(path: str | Path | None = None) -> TPUCatalog:
    source_path = _catalog_path(path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"TPU glossary snapshot not found: {source_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid TPU glossary JSON: {source_path}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError("TPU glossary root must be a JSON object")
    if raw.get("schema_version") != TPU_SCHEMA_VERSION:
        raise RuntimeError(
            f"unsupported TPU glossary schema_version: {raw.get('schema_version')!r}"
        )

    tpu_version = str(raw.get("tpu_version") or "").strip()
    publisher = str(raw.get("publisher") or "").strip()
    source = str(raw.get("source") or "").strip()
    source_version_label = str(raw.get("source_version_label") or "").strip()
    if not all((tpu_version, publisher, source, source_version_label)):
        raise RuntimeError("TPU glossary snapshot is missing source/version metadata")

    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list):
        raise RuntimeError("TPU glossary entries must be a JSON array")

    entries: dict[tuple[TPUKind, str], TPUDefinition] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise RuntimeError("TPU glossary entry must be a JSON object")
        kind = str(raw_entry.get("kind") or "").strip()
        if kind not in {"class", "subject"}:
            raise RuntimeError(f"invalid TPU glossary kind: {kind!r}")
        code = str(raw_entry.get("code") or "").strip()
        name = str(raw_entry.get("name") or "").strip()
        definition = str(raw_entry.get("definition") or "").strip()
        source_ref = str(raw_entry.get("source_ref") or "").strip()
        if not all((code, name, definition, source_ref)):
            raise RuntimeError("TPU glossary entry is missing required fields")
        key = (kind, code)
        if key in entries:
            raise RuntimeError(f"duplicate TPU glossary entry: {kind}:{code}")
        entries[key] = TPUDefinition(
            kind=kind,  # type: ignore[arg-type]
            code=code,
            name=name,
            definition=definition,
            tpu_version=tpu_version,
            publisher=publisher,
            source=source,
            source_ref=source_ref,
        )

    return TPUCatalog(
        tpu_version=tpu_version,
        publisher=publisher,
        source=source,
        source_version_label=source_version_label,
        entries=entries,
    )


@lru_cache(maxsize=4)
def _cached_catalog(path: str) -> TPUCatalog:
    return load_tpu_catalog(path)


def get_tpu_catalog() -> TPUCatalog:
    return _cached_catalog(str(_catalog_path().resolve()))


def clear_tpu_catalog_cache() -> None:
    _cached_catalog.cache_clear()


def _subject_code(subject: Any) -> str | None:
    if not isinstance(subject, dict):
        return None
    value = subject.get("code")
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def resolve_process_tpu_definitions(
    *,
    class_code: str | int | None,
    subjects: Sequence[Any],
    catalog: TPUCatalog | None = None,
) -> list[TPUDefinition]:
    active = catalog or get_tpu_catalog()
    resolved: list[TPUDefinition] = []

    class_definition = active.resolve(kind="class", code=class_code)
    if class_definition is not None:
        resolved.append(class_definition)

    seen_subject_codes: set[str] = set()
    for subject in subjects:
        code = _subject_code(subject)
        if code is None or code in seen_subject_codes:
            continue
        seen_subject_codes.add(code)
        definition = active.resolve(kind="subject", code=code)
        if definition is not None:
            resolved.append(definition)
    return resolved


def resolve_process_tpu_context(
    *,
    class_code: str | int | None,
    subjects: Sequence[Any],
    catalog: TPUCatalog | None = None,
) -> list[dict[str, str]]:
    """Resolve only codes present in the process against the pinned local snapshot.

    Unknown or malformed codes are omitted. No name matching, fuzzy completion or
    network fallback is attempted, so the glossary cannot silently invent a
    definition that is absent from the versioned source.
    """
    return [
        definition.as_context()
        for definition in resolve_process_tpu_definitions(
            class_code=class_code,
            subjects=subjects,
            catalog=catalog,
        )
    ]
