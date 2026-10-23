from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Sequence

TPU_GLOSSARY_PATH_ENV = "TPU_GLOSSARY_PATH"
TPU_SCHEMA_VERSION = 1
TPU_SNAPSHOT_RELATIVE_PATH = Path("data") / "tpu" / "2026-09-12.json"
LEGAL_KNOWLEDGE_RELATIVE_PATH = Path("data") / "legal-knowledge" / "2026-10-22.json"
TPUKind = Literal["class", "subject"]
KnowledgeKind = Literal["class", "subject", "movement", "concept"]


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
            "kind": self.kind, "code": self.code, "name": self.name,
            "definition": self.definition, "tpu_version": self.tpu_version,
            "publisher": self.publisher, "source": self.source,
            "source_ref": self.source_ref,
            "definition_sha256": self.definition_sha256,
        }

    def as_provenance(self, *, source_order: int) -> dict[str, str | int]:
        return {
            "kind": self.kind, "code": self.code, "tpu_version": self.tpu_version,
            "publisher": self.publisher, "source": self.source,
            "source_ref": self.source_ref,
            "definition_sha256": self.definition_sha256, "source_order": source_order,
        }


@dataclass(frozen=True, slots=True)
class LegalKnowledgeEntry:
    id: str
    kind: KnowledgeKind
    code: str
    name: str
    definition: str
    aliases: tuple[str, ...]
    source_ref: str
    source_document: str
    definition_sha256: str
    donor_mapping_verified_by_cnj: bool = False

    @property
    def embedding_sha256(self) -> str:
        text = " | ".join((self.name, *self.aliases, self.definition))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def as_context(self, *, match_method: str, score: float = 1.0) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "code": self.code,
            "name": self.name, "definition": self.definition,
            "aliases": list(self.aliases), "source_ref": self.source_ref,
            "source_document": self.source_document,
            "publisher": "RAgvia — legislação identificada; não é tabela CNJ",
            "source": "legislative primary source excerpts",
            "tpu_version": "ragvia-legal-m02-v1",
            "definition_sha256": self.definition_sha256,
            "donor_mapping_verified_by_cnj": self.donor_mapping_verified_by_cnj,
            "match_method": match_method, "score": score,
        }

    def as_provenance(self, *, source_order: int) -> dict[str, Any]:
        return {
            "kind": self.kind if self.kind in {"class", "subject"} else "concept",
            "code": self.code, "tpu_version": "ragvia-legal-m02-v1",
            "publisher": "RAgvia — legislação identificada; não é tabela CNJ",
            "source": "legislative primary source excerpts",
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
    return Path(__file__).resolve().parent.parent / TPU_SNAPSHOT_RELATIVE_PATH


def _catalog_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = str(os.environ.get(TPU_GLOSSARY_PATH_ENV) or "").strip()
    return Path(configured) if configured else _default_catalog_path()


def _validate_catalog(raw: Any) -> TPUCatalog:
    if not isinstance(raw, dict) or raw.get("schema_version") != TPU_SCHEMA_VERSION:
        raise RuntimeError("unsupported TPU glossary schema_version")
    version = str(raw.get("tpu_version") or "").strip()
    publisher = str(raw.get("publisher") or "").strip()
    source = str(raw.get("source") or "").strip()
    version_label = str(raw.get("source_version_label") or "").strip()
    raw_entries = raw.get("entries")
    if not all((version, publisher, source, version_label)) or not isinstance(raw_entries, list):
        raise RuntimeError("TPU glossary snapshot is missing source/version metadata")
    entries: dict[tuple[TPUKind, str], TPUDefinition] = {}
    for item in raw_entries:
        if not isinstance(item, dict):
            raise RuntimeError("TPU glossary entry must be an object")
        kind, code = str(item.get("kind") or "").strip(), str(item.get("code") or "").strip()
        name, definition, source_ref = (str(item.get(key) or "").strip() for key in ("name", "definition", "source_ref"))
        if kind not in {"class", "subject"} or not all((code, name, definition, source_ref)):
            raise RuntimeError("invalid TPU glossary entry")
        key = (kind, code)
        if key in entries:
            raise RuntimeError(f"duplicate TPU glossary entry: {kind}:{code}")
        entries[key] = TPUDefinition(kind, code, name, definition, version, publisher, source, source_ref)  # type: ignore[arg-type]
    return TPUCatalog(version, publisher, source, version_label, entries)


def load_tpu_catalog(path: str | Path | None = None) -> TPUCatalog:
    source_path = _catalog_path(path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"TPU glossary snapshot not found: {source_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid TPU glossary JSON: {source_path}") from exc
    return _validate_catalog(raw)


@lru_cache(maxsize=4)
def _cached_catalog(path: str) -> TPUCatalog:
    return load_tpu_catalog(path)


def get_tpu_catalog() -> TPUCatalog:
    return _cached_catalog(str(_catalog_path().resolve()))


def clear_tpu_catalog_cache() -> None:
    _cached_catalog.cache_clear()


def _subject_code(subject: Any) -> str | None:
    if not isinstance(subject, dict) or subject.get("code") is None:
        return None
    return str(subject["code"]).strip() or None


def resolve_process_tpu_definitions(*, class_code: str | int | None, subjects: Sequence[Any], catalog: TPUCatalog | None = None, class_name: str | None = None) -> list[TPUDefinition]:
    active = catalog or get_tpu_catalog()
    result: list[TPUDefinition] = []
    definition = active.resolve(kind="class", code=class_code)
    if definition is None and class_name:
        definition = resolve_official_tpu_alias(kind="class", name=class_name, catalog=active)
    if definition:
        result.append(definition)
    seen: set[str] = set()
    for subject in subjects:
        code = _subject_code(subject)
        if code is None or code in seen:
            continue
        seen.add(code)
        definition = active.resolve(kind="subject", code=code)
        if definition:
            result.append(definition)
    return result


def resolve_official_tpu_alias(*, kind: TPUKind, name: str, catalog: TPUCatalog | None = None) -> TPUDefinition | None:
    active = catalog or get_tpu_catalog()
    raw = json.loads(_knowledge_path().read_text(encoding="utf-8"))
    aliases = raw.get("supplemental_aliases", {}) if isinstance(raw, dict) else {}
    if not isinstance(aliases, dict):
        raise RuntimeError("legal knowledge supplemental_aliases must be an object")
    target = normalize_legal_term(name)
    for key, values in aliases.items():
        alias_kind, sep, code = str(key).partition(":")
        if not sep or alias_kind != kind or not isinstance(values, dict):
            raise RuntimeError("invalid supplemental TPU alias reference")
        if values.get("verified_by_cnj") is not False or not isinstance(values.get("aliases"), list):
            raise RuntimeError("supplemental TPU alias must remain explicitly unverified")
        alias_values = values["aliases"]
        definition = active.resolve(kind=kind, code=code)
        if definition is None:
            raise RuntimeError("official TPU alias references an unknown code")
        if any(not isinstance(value, str) or not value.strip() for value in alias_values):
            raise RuntimeError("invalid supplemental TPU alias value")
        if target in {normalize_legal_term(value) for value in alias_values}:
            return definition
    return None


def resolve_process_tpu_context(*, class_code: str | int | None, subjects: Sequence[Any], catalog: TPUCatalog | None = None, class_name: str | None = None) -> list[dict[str, str]]:
    return [entry.as_context() for entry in resolve_process_tpu_definitions(class_code=class_code, subjects=subjects, catalog=catalog, class_name=class_name)]


def normalize_legal_term(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _knowledge_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    runtime = Path.cwd() / LEGAL_KNOWLEDGE_RELATIVE_PATH
    return runtime if runtime.is_file() else Path(__file__).resolve().parent.parent / LEGAL_KNOWLEDGE_RELATIVE_PATH


def load_legal_knowledge(path: str | Path | None = None) -> tuple[LegalKnowledgeEntry, ...]:
    source_path = _knowledge_path(path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("legal knowledge snapshot is unavailable or invalid") from exc
    if (not isinstance(raw, dict) or raw.get("schema_version") != 1
            or raw.get("tpu_version") != "ragvia-legal-m02-v1"
            or not str(raw.get("publisher") or "").strip()
            or not str(raw.get("source") or "").strip()
            or not str(raw.get("source_version_label") or "").strip()
            or not isinstance(raw.get("entries"), list)):
        raise RuntimeError("invalid legal knowledge snapshot schema")
    try:
        source_manifest = json.loads((source_path.parent / "sources.json").read_text(encoding="utf-8"))
        source_urls = {str(item["id"]): str(item["url"]) for item in source_manifest["documents"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("legal knowledge source manifest is missing or invalid") from exc
    entries: list[LegalKnowledgeEntry] = []
    seen: set[str] = set()
    for row in raw["entries"]:
        if not isinstance(row, dict):
            raise RuntimeError("legal knowledge entry must be an object")
        entry_id = str(row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip()
        name = str(row.get("name") or "").strip()
        definition = str(row.get("definition") or "").strip()
        source_ref = str(row.get("source_ref") or "").strip()
        document = str(row.get("source_document") or "").strip()
        code = str(row.get("code") or "").strip()
        aliases = row.get("aliases", [])
        source_url = source_ref.split(" — ", 1)[0]
        if (entry_id in seen or kind not in {"class", "subject", "movement", "concept"}
                or not all((entry_id, name, definition, source_ref, document, code))
                or not isinstance(aliases, list)
                or any(not isinstance(a, str) or not a.strip() for a in aliases)
                or source_urls.get(document) != source_url or not source_url.startswith("https://")):
            raise RuntimeError("invalid, duplicate, or unprovenanced legal knowledge entry")
        seen.add(entry_id)
        digest = hashlib.sha256(definition.encode("utf-8")).hexdigest()
        mapping_verified = row.get("donor_mapping_verified_by_cnj", False)
        if mapping_verified is not False:
            raise RuntimeError("donor class/subject mappings require independent CNJ verification")
        entries.append(LegalKnowledgeEntry(entry_id, kind, code, name, definition, tuple(aliases), source_ref, document, digest, False))
    return tuple(entries)


def rank_legal_knowledge_candidates(*, entries: Sequence[LegalKnowledgeEntry], scores: dict[str, float], k: int, kinds: Sequence[KnowledgeKind] | None = None, min_score: float = 0.25) -> list[tuple[LegalKnowledgeEntry, float]]:
    if k <= 0:
        return []
    allowed = set(kinds) if kinds is not None else None
    ranked = [(entry, float(scores[entry.id])) for entry in entries
              if entry.id in scores and (allowed is None or entry.kind in allowed)]
    ranked = [(entry, score) for entry, score in ranked if score >= min_score]
    return sorted(ranked, key=lambda item: (-item[1], item[0].id))[:k]


def resolve_legal_knowledge(*, class_code: str | int | None, class_name: str | None, subjects: Sequence[Any], movements: Sequence[dict[str, Any]], catalog: TPUCatalog | None = None, entries: Sequence[LegalKnowledgeEntry] | None = None, semantic_scores: dict[str, dict[str, float]] | None = None, concept_queries: Sequence[str] | None = None) -> list[dict[str, Any]]:
    official = catalog or get_tpu_catalog()
    knowledge = tuple(entries) if entries is not None else load_legal_knowledge()
    by_kind_code: dict[tuple[str, str], LegalKnowledgeEntry] = {}
    by_kind_term: dict[tuple[str, str], LegalKnowledgeEntry] = {}
    for entry in knowledge:
        by_kind_code.setdefault((entry.kind, entry.code), entry)
        for term in (entry.name, *entry.aliases):
            by_kind_term.setdefault((entry.kind, normalize_legal_term(term)), entry)
    selected: dict[str, tuple[LegalKnowledgeEntry, str, float]] = {}

    def add(entry: LegalKnowledgeEntry | None, method: str, score: float = 1.0) -> None:
        if entry is not None:
            selected.setdefault(entry.id, (entry, method, score))

    def match(kind: str, name: str, code: str | int | None = None, *, allow_semantic: bool = False) -> None:
        if kind in {"class", "subject"} and code is not None:
            official_definition = official.resolve(kind=kind, code=code)
            if official_definition:
                # A recognized official code is authoritative; do not reinterpret its name.
                return
            code_match = by_kind_code.get((kind, str(code).strip()))
            if code_match is not None:
                add(code_match, "code")
                return
        term = by_kind_term.get((kind, normalize_legal_term(name)))
        if term:
            add(term, "term")
            return
        if not allow_semantic:
            return
        for candidate, score in rank_legal_knowledge_candidates(
            entries=knowledge, scores=(semantic_scores or {}).get(normalize_legal_term(name), {}),
            k=1, kinds=(kind,), min_score=0.35,
        ):
            add(candidate, "semantic_bge", round(score, 3))

    if class_name:
        match("class", str(class_name), class_code, allow_semantic=True)
    for subject in subjects:
        if isinstance(subject, dict):
            name = str(subject.get("name") or "").strip()
            if name:
                match("subject", name, subject.get("code"), allow_semantic=True)
    for movement in movements:
        if not isinstance(movement, dict):
            continue
        text = str(movement.get("text") or movement.get("title") or "").strip()
        if text:
            normalized = normalize_legal_term(text)
            exact = next((e for e in knowledge if e.kind == "movement" and (normalize_legal_term(e.name) in normalized or any(normalize_legal_term(alias) in normalized for alias in e.aliases))), None)
            if exact:
                add(exact, "term")
            else:
                scores = (semantic_scores or {}).get(normalize_legal_term(text), {})
                for entry, score in rank_legal_knowledge_candidates(
                    entries=knowledge, scores=scores, k=2, kinds=("movement",), min_score=0.25,
                ):
                    add(entry, "semantic_bge", round(score, 3))
    # Generic concepts are exact-match selected; semantic fallback is queried only for misses.
    for query in concept_queries if concept_queries is not None else ("POLO ATIVO E POLO PASSIVO", "ADVOGADO", "VALOR DA CAUSA", "PRAZOS NO JUIZADO ESPECIAL"):
        exact = by_kind_term.get(("concept", normalize_legal_term(query)))
        if exact:
            add(exact, "term")
        else:
            scores = (semantic_scores or {}).get(normalize_legal_term(query), {})
            for entry, score in rank_legal_knowledge_candidates(
                entries=knowledge, scores=scores, k=1, kinds=("concept",), min_score=0.2,
            ):
                add(entry, "semantic_bge", round(score, 3))
    return [entry.as_context(match_method=method, score=score) for entry, method, score in selected.values()]
