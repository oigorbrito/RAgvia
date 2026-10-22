from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Sequence

SourceName = Literal["judit", "datajud"]


@dataclass(frozen=True, slots=True)
class DataJudMetadata:
    """Normalized, transport-independent metadata returned by an authorized DataJud adapter."""

    class_name: str | None = None
    class_code: str | None = None
    subjects: tuple[dict[str, str], ...] = ()
    adjudicating_body: str | None = None
    county: str | None = None
    source_ref: str = "DataJud/CNJ"


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    field: str
    selected_source: SourceName
    selected_value: Any
    conflict: bool
    source_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "selected_source": self.selected_source,
            "selected_value": self.selected_value,
            "conflict": self.conflict,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class DataJudMergeResult:
    process: dict[str, Any]
    provenance: tuple[FieldProvenance, ...]
    warnings: tuple[str, ...]
    datajud_applied: bool


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _subject(subject: Any) -> dict[str, str] | None:
    if not isinstance(subject, dict):
        return None
    code = _clean(subject.get("code"))
    name = _clean(subject.get("name"))
    if not code and not name:
        return None
    result: dict[str, str] = {}
    if code:
        result["code"] = code
    if name:
        result["name"] = name
    return result


def normalize_subjects(subjects: Sequence[Any]) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for raw in subjects:
        item = _subject(raw)
        if item is None:
            continue
        key = (item.get("code"), item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return tuple(normalized)


def _subject_identity(subjects: Sequence[Any]) -> set[tuple[str | None, str | None]]:
    return {
        (item.get("code"), item.get("name"))
        for item in normalize_subjects(subjects)
    }


def _different(left: Any, right: Any) -> bool:
    left_value = _clean(left)
    right_value = _clean(right)
    return bool(left_value and right_value and left_value.casefold() != right_value.casefold())


def _warning(field: str) -> str:
    return (
        f"Conflito de metadados entre Judit e DataJud no campo {field}; "
        "o valor oficial do DataJud foi usado no contexto normalizado."
    )


def _select_scalar(
    *,
    field: str,
    judit_value: Any,
    datajud_value: Any,
    source_ref: str,
) -> tuple[Any, FieldProvenance, str | None]:
    official = _clean(datajud_value)
    current = _clean(judit_value)
    if official is None:
        return (
            judit_value,
            FieldProvenance(
                field=field,
                selected_source="judit",
                selected_value=judit_value,
                conflict=False,
                source_ref=None,
            ),
            None,
        )

    conflict = _different(current, official)
    return (
        official,
        FieldProvenance(
            field=field,
            selected_source="datajud",
            selected_value=official,
            conflict=conflict,
            source_ref=source_ref,
        ),
        _warning(field) if conflict else None,
    )


def merge_datajud_metadata(
    judit_process: dict[str, Any],
    datajud: DataJudMetadata | None,
) -> DataJudMergeResult:
    """Merge official metadata without mutating Judit-owned parties/movements.

    The input is the normalized/promotable Judit structure, not a raw provider payload.
    DataJud is authoritative only for the metadata fields explicitly modeled here.
    """

    merged = deepcopy(judit_process)
    secrecy_level = int(merged.get("secrecy_level") or 0)
    if datajud is None or secrecy_level > 0:
        return DataJudMergeResult(
            process=merged,
            provenance=(),
            warnings=(),
            datajud_applied=False,
        )

    header = deepcopy(merged.get("header") or {})
    provenance: list[FieldProvenance] = []
    warnings: list[str] = []

    class_name, item, warning = _select_scalar(
        field="class_name",
        judit_value=merged.get("class_name"),
        datajud_value=datajud.class_name,
        source_ref=datajud.source_ref,
    )
    merged["class_name"] = class_name
    provenance.append(item)
    if warning:
        warnings.append(warning)

    class_code, item, warning = _select_scalar(
        field="class_code",
        judit_value=header.get("class_code"),
        datajud_value=datajud.class_code,
        source_ref=datajud.source_ref,
    )
    if class_code is not None:
        header["class_code"] = class_code
    provenance.append(item)
    if warning:
        warnings.append(warning)

    adjudicating_body, item, warning = _select_scalar(
        field="adjudicating_body",
        judit_value=header.get("adjudicating_body"),
        datajud_value=datajud.adjudicating_body,
        source_ref=datajud.source_ref,
    )
    if adjudicating_body is not None:
        header["adjudicating_body"] = adjudicating_body
    provenance.append(item)
    if warning:
        warnings.append(warning)

    county, item, warning = _select_scalar(
        field="county",
        judit_value=header.get("county"),
        datajud_value=datajud.county,
        source_ref=datajud.source_ref,
    )
    if county is not None:
        header["county"] = county
    provenance.append(item)
    if warning:
        warnings.append(warning)

    judit_subjects = normalize_subjects(merged.get("subjects") or [])
    datajud_subjects = normalize_subjects(datajud.subjects)
    if datajud_subjects:
        conflict = bool(judit_subjects) and (
            _subject_identity(judit_subjects) != _subject_identity(datajud_subjects)
        )
        merged["subjects"] = [dict(item) for item in datajud_subjects]
        provenance.append(
            FieldProvenance(
                field="subjects",
                selected_source="datajud",
                selected_value=[dict(item) for item in datajud_subjects],
                conflict=conflict,
                source_ref=datajud.source_ref,
            )
        )
        if conflict:
            warnings.append(_warning("subjects"))
    else:
        merged["subjects"] = [dict(item) for item in judit_subjects]
        provenance.append(
            FieldProvenance(
                field="subjects",
                selected_source="judit",
                selected_value=[dict(item) for item in judit_subjects],
                conflict=False,
                source_ref=None,
            )
        )

    merged["header"] = header

    return DataJudMergeResult(
        process=merged,
        provenance=tuple(provenance),
        warnings=tuple(warnings),
        datajud_applied=True,
    )
