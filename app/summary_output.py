from __future__ import annotations

import json
import re
from typing import Any

from app.json_utils import loads_strict_json

from app.summary_contract import CONDITIONAL_SUMMARY_FIELDS

SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "synthesis": {"type": "string"},
        "timeline": {
            "type": "array",
            "items": {"type": "string"},
        },
        "current_status": {"type": "string"},
        "attention": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "decisions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "deadlines": {
            "type": "array",
            "items": {"type": "string"},
        },
        "related_processes": {
            "type": "array",
            "items": {"type": "string"},
        },
        "attachments": {
            "type": "array",
            "items": {"type": "string"},
        },
        "claims": {
            "type": "array",
            "maxItems": 96,
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string", "maxLength": 64},
                    "text": {"type": "string", "maxLength": 6000},
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "uniqueItems": True,
                        "items": {"type": "string", "maxLength": 34},
                    },
                },
                "required": ["claim_id", "text", "evidence_refs"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "synthesis",
        "timeline",
        "current_status",
        "attention",
        "decisions",
        "deadlines",
        "related_processes",
        "attachments",
        "claims",
    ],
    "additionalProperties": False,
}

_HEADER_FIELDS = (
    ("instance", "Instância"),
    ("area", "Área"),
    ("justice_description", "Justiça"),
    ("county", "Comarca"),
    ("state", "Estado"),
    ("city", "Cidade"),
    ("amount", "Valor"),
    ("court_unit", "Órgão julgador registrado"),
    ("judge", "Magistrado(a) registrado(a)"),
    ("phase", "Fase registrada"),
    ("status", "Situação registrada"),
    ("distribution_date", "Data de distribuição registrada"),
)
_LIST_FIELDS = (("timeline", "Movimentações"), *CONDITIONAL_SUMMARY_FIELDS)
_REQUIRED_KEYS = frozenset(SUMMARY_OUTPUT_SCHEMA["required"])
_DOCUMENT_KEYS = frozenset({"schema_version", "process", "summary"})
_PROCESS_REQUIRED_KEYS = frozenset({"cnj", "class_name", "court", "header", "parties"})
_PROCESS_KEYS = _PROCESS_REQUIRED_KEYS | {"representatives"}
_PARTY_KEYS = frozenset({"name", "side", "person_type", "masked_person_id"})
_REPRESENTATIVE_KEYS = _PARTY_KEYS | {"represents"}
_HEADER_KEYS = frozenset(key for key, _ in _HEADER_FIELDS)
_SPACE_RE = re.compile(r"\s+")
_MAX_SYNTHESIS_CHARS = 6000
_MAX_STATUS_CHARS = 4000
_MAX_ITEM_CHARS = 2000
_MAX_ITEMS = {
    "timeline": 24,
    "attention": 12,
    "decisions": 16,
    "deadlines": 12,
    "related_processes": 12,
    "attachments": 12,
}


def _inline(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _prose_line(value: Any) -> str:
    text = _inline(value)
    if text.startswith(("#", "<", "`", "- ", "* ")):
        return "\u2060" + text
    return text


def _string_list(value: Any, *, key: str, require_nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"structured summary field must be a string array: {key}")
    if len(value) > _MAX_ITEMS[key]:
        raise ValueError(f"structured summary field has too many items: {key}")
    rendered = [_prose_line(item) for item in value if _inline(item)]
    if any(len(item) > _MAX_ITEM_CHARS for item in rendered):
        raise ValueError(f"structured summary item is too long: {key}")
    if require_nonempty and not rendered:
        raise ValueError(f"structured summary field must not be empty: {key}")
    return rendered


def _claims(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 96:
        raise ValueError("structured summary claims must be a bounded array")
    claims: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "claim_id",
            "text",
            "evidence_refs",
        }:
            raise ValueError("structured summary claim keys do not match the contract")
        claim_id = _inline(item.get("claim_id"))
        text = item.get("text")
        refs = item.get("evidence_refs")
        if not claim_id or len(claim_id) > 64:
            raise ValueError("structured summary claim_id is invalid")
        if not isinstance(text, str) or not _inline(text):
            raise ValueError("structured summary claim text must be non-empty")
        if len(text) > _MAX_SYNTHESIS_CHARS:
            raise ValueError("structured summary claim text is too long")
        if (
            not isinstance(refs, list)
            or not refs
            or len(refs) > 32
            or any(not isinstance(ref, str) or not _inline(ref) for ref in refs)
        ):
            raise ValueError("structured summary claim evidence_refs are invalid")
        rendered_refs = [_inline(ref) for ref in refs]
        if len(set(rendered_refs)) != len(rendered_refs):
            raise ValueError("structured summary claim evidence_refs must be unique")
        claims.append(
            {
                "claim_id": claim_id,
                "text": _prose_line(text),
                "evidence_refs": rendered_refs,
            }
        )
    return claims


def parse_structured_summary(raw: str) -> dict[str, Any]:
    try:
        payload = loads_strict_json(raw)
    except ValueError as exc:
        raise ValueError("provider structured summary is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("provider structured summary must be a JSON object")
    legacy_required = _REQUIRED_KEYS - {"claims"}
    if not legacy_required.issubset(payload) or not set(payload).issubset(_REQUIRED_KEYS):
        raise ValueError("provider structured summary keys do not match the contract")

    synthesis = payload.get("synthesis")
    current_status = payload.get("current_status")
    if not isinstance(synthesis, str) or not _inline(synthesis):
        raise ValueError("structured summary synthesis must be a non-empty string")
    if len(synthesis) > _MAX_SYNTHESIS_CHARS:
        raise ValueError("structured summary synthesis is too long")
    if not isinstance(current_status, str) or not _inline(current_status):
        raise ValueError("structured summary current_status must be a non-empty string")
    if len(current_status) > _MAX_STATUS_CHARS:
        raise ValueError("structured summary current_status is too long")

    return {
        "synthesis": _prose_line(synthesis),
        "timeline": _string_list(payload.get("timeline"), key="timeline"),
        "current_status": _prose_line(current_status),
        "attention": _string_list(
            payload.get("attention"), key="attention", require_nonempty=True
        ),
        "decisions": _string_list(payload.get("decisions"), key="decisions"),
        "deadlines": _string_list(payload.get("deadlines"), key="deadlines"),
        "related_processes": _string_list(
            payload.get("related_processes"), key="related_processes"
        ),
        "attachments": _string_list(payload.get("attachments"), key="attachments"),
        "claims": _claims(payload.get("claims", [])),
    }


def structured_summary_document_is_canonical(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _DOCUMENT_KEYS:
        return False
    schema_version = value.get("schema_version")
    if schema_version not in {2, 3}:
        return False

    process = value.get("process")
    expected_process_keys = (
        _PROCESS_REQUIRED_KEYS if schema_version == 2 else _PROCESS_KEYS
    )
    if not isinstance(process, dict) or set(process) != expected_process_keys:
        return False
    if not isinstance(process.get("cnj"), str):
        return False
    if process.get("class_name") is not None and not isinstance(
        process.get("class_name"), str
    ):
        return False
    if process.get("court") is not None and not isinstance(process.get("court"), str):
        return False

    header = process.get("header")
    if not isinstance(header, dict) or not set(header).issubset(_HEADER_KEYS):
        return False
    if any(isinstance(item, (dict, list)) for item in header.values()):
        return False

    parties = process.get("parties")
    if not isinstance(parties, list):
        return False
    for party in parties:
        if (
            not isinstance(party, dict)
            or not set(party).issubset(_PARTY_KEYS)
            or any(not isinstance(item, str) for item in party.values())
        ):
            return False

    representatives = process.get("representatives", [])
    if not isinstance(representatives, list):
        return False
    for representative in representatives:
        if (
            not isinstance(representative, dict)
            or not set(representative).issubset(_REPRESENTATIVE_KEYS)
            or any(not isinstance(item, str) for item in representative.values())
        ):
            return False

    summary = value.get("summary")
    if not isinstance(summary, dict) or set(summary) != _REQUIRED_KEYS:
        return False
    try:
        normalized = parse_structured_summary(
            json.dumps(summary, ensure_ascii=False)
        )
    except (TypeError, ValueError):
        return False
    return normalized == summary


def _append_list(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.extend(["", f"## {title}", *(f"- {item}" for item in items)])


def structured_summary_process(context: dict[str, Any]) -> dict[str, Any]:
    header = context.get("header") if isinstance(context.get("header"), dict) else {}
    parties = context.get("parties")
    normalized_parties = (
        [
            {
                key: item.get(key)
                for key in ("name", "side", "person_type", "masked_person_id")
                if item.get(key) is not None
            }
            for item in parties
            if isinstance(item, dict)
        ]
        if isinstance(parties, list)
        else []
    )
    known_party_names = {
        _inline(item.get("name")).casefold()
        for item in normalized_parties
        if item.get("name")
    }
    raw_representatives = context.get("representatives")
    normalized_representatives = []
    if isinstance(raw_representatives, list):
        for item in raw_representatives:
            if not isinstance(item, dict) or not _inline(item.get("name")):
                continue
            representative = {
                key: _inline(item.get(key))
                for key in ("name", "side", "person_type", "masked_person_id")
                if item.get(key) is not None and _inline(item.get(key))
            }
            represents = _inline(item.get("represents"))
            if represents and represents.casefold() in known_party_names:
                representative["represents"] = represents
            normalized_representatives.append(representative)
    return {
        "cnj": _inline(context.get("code")),
        "class_name": _inline(context.get("class_name")) or None,
        "court": _inline(context.get("court")) or None,
        "header": {
            key: header.get(key)
            for key, _ in _HEADER_FIELDS
            if header.get(key) is not None
        },
        "parties": normalized_parties,
        "representatives": normalized_representatives,
    }


def structured_summary_document_matches_process(
    value: Any,
    context: dict[str, Any],
) -> bool:
    if not structured_summary_document_is_canonical(value):
        return False
    expected_process = structured_summary_process(context)
    actual_process = value["process"]
    if value["schema_version"] == 2:
        expected_process.pop("representatives")
    return actual_process == expected_process


def structured_summary_document(
    payload: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "process": structured_summary_process(context),
        "summary": {
            "synthesis": payload["synthesis"],
            "timeline": list(payload["timeline"]),
            "current_status": payload["current_status"],
            "attention": list(payload["attention"]),
            "decisions": list(payload["decisions"]),
            "deadlines": list(payload["deadlines"]),
            "related_processes": list(payload["related_processes"]),
            "attachments": list(payload["attachments"]),
            "claims": [dict(item) for item in payload.get("claims", [])],
        },
    }


def _subject_names(context: dict[str, Any]) -> list[str]:
    subjects = context.get("subjects")
    if not isinstance(subjects, list):
        return []
    names: list[str] = []
    for item in subjects:
        if isinstance(item, dict):
            name = _inline(item.get("name") or item.get("label") or item.get("description"))
        else:
            name = _inline(item)
        if name:
            names.append(name)
    return names


def render_structured_summary(payload: dict[str, Any], context: dict[str, Any]) -> str:
    lines = ["# Resumo do processo", "", '<ProcessHeader className="process-header">']
    lines.append(f"- Processo: {_inline(context.get('code'))}")

    class_name = _inline(context.get("class_name"))
    if class_name:
        lines.append(f"- Classe: {class_name}")
    court = _inline(context.get("court"))
    if court:
        lines.append(f"- Tribunal: {court}")

    header = context.get("header") if isinstance(context.get("header"), dict) else {}
    for key, label in _HEADER_FIELDS:
        rendered = _inline(header.get(key))
        if rendered:
            lines.append(f"- {label}: {rendered}")
    lines.append("</ProcessHeader>")

    lines.extend(["", payload["synthesis"]])

    parties = context.get("parties")
    party_names = (
        [
            _inline(item.get("name") or item.get("nome"))
            for item in parties
            if isinstance(item, dict)
        ]
        if isinstance(parties, list)
        else []
    )
    party_names = [name for name in party_names if name]
    lines.extend(["", "## Partes"])
    lines.extend((f"- {name}" for name in party_names) if party_names else ["Não informado."])

    representatives = structured_summary_process(context)["representatives"]
    representative_lines = []
    for representative in representatives:
        name = _inline(representative.get("name"))
        represents = _inline(representative.get("represents"))
        if name:
            label = f"{name} (representa: {represents})" if represents else name
            representative_lines.append(f"- {label}")
    lines.extend(["", "## Representantes"])
    lines.extend(representative_lines or ["Não informado."])

    lines.extend(["", "## Classe", class_name or "Não informado."])

    subject_names = _subject_names(context)
    lines.extend(["", "## Assuntos"])
    lines.extend((f"- {name}" for name in subject_names) if subject_names else ["Não informado."])

    lines.extend(["", "## Movimentações"])
    if payload["timeline"]:
        lines.extend(f"- {item}" for item in payload["timeline"])
    else:
        lines.append("Nenhuma movimentação relevante selecionada.")
    lines.extend(["", f"Estado atual: {payload['current_status']}"])

    lines.extend(
        ["", "## Pontos de atenção", *(f"- {item}" for item in payload["attention"])]
    )

    for key, title in CONDITIONAL_SUMMARY_FIELDS:
        _append_list(lines, title, payload[key])

    return "\n".join(lines).strip()
