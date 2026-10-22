from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

SEMANTIC_SCHEMA_VERSION = 3
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _datetime_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_SAO_PAULO).isoformat()


def _step_semantics(step: dict[str, Any]) -> dict[str, Any]:
    metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
    return {
        "step_number": int(step["step_number"]),
        "occurred_at": _datetime_value(step.get("occurred_at")),
        "title": step.get("title"),
        "text": str(step.get("text") or ""),
        "source_step_number": metadata.get("source_step_number"),
        "private": metadata.get("private"),
        "secrecy_level": metadata.get("secrecy_level"),
        "tags": metadata.get("tags") or {},
    }


def _attachment_semantics(attachment: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachment_id": str(attachment.get("attachment_id") or ""),
        "attachment_date": _datetime_value(attachment.get("attachment_date")),
        "attachment_name": attachment.get("attachment_name"),
        "provider_status": attachment.get("provider_status"),
    }


def semantic_document(
    *,
    header: dict[str, Any],
    parties: list[dict[str, Any]],
    subjects: list[Any],
    steps: list[dict[str, Any]],
    court: str | None,
    class_name: str | None,
    secrecy_level: int,
    representatives: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = attachments or []
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "court": court,
        "class_name": class_name,
        "secrecy_level": int(secrecy_level),
        "header": header,
        "parties": parties,
        "representatives": representatives or [],
        "subjects": subjects,
        "steps": [_step_semantics(step) for step in steps],
        "attachments": [_attachment_semantics(item) for item in manifest],
    }


def semantic_fingerprint(**fields: Any) -> str:
    payload = semantic_document(**fields)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
