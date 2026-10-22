from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ATTACHMENT_STATUS_KEYS = (
    "pending",
    "ready",
    "unavailable",
    "corrupt",
    "unreadable",
)
_FAILURE_STATUS_KEYS = ("unavailable", "corrupt", "unreadable")


def normalize_attachment_status_counts(
    counts: Mapping[str, Any] | None,
) -> dict[str, int]:
    normalized = {key: 0 for key in ATTACHMENT_STATUS_KEYS}
    if counts:
        for key in ATTACHMENT_STATUS_KEYS:
            raw = counts.get(key, 0)
            try:
                value = int(raw or 0)
            except (TypeError, ValueError):
                value = 0
            normalized[key] = max(0, value)
    return normalized


def attachment_status_flags(
    counts: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized = normalize_attachment_status_counts(counts)
    total = sum(normalized.values())
    if total == 0:
        return {}

    failed = sum(normalized[key] for key in _FAILURE_STATUS_KEYS)
    return {
        "attachments": {
            "total": total,
            "status_counts": normalized,
            "processing_complete": normalized["pending"] == 0,
            "degraded": failed > 0,
        }
    }


def attachment_status_warnings(
    counts: Mapping[str, Any] | None,
) -> list[str]:
    normalized = normalize_attachment_status_counts(counts)
    warnings: list[str] = []

    templates = (
        ("pending", "Há anexos pendentes de processamento: {count}."),
        ("unavailable", "Há anexos indisponíveis na fonte: {count}."),
        ("corrupt", "Há anexos corrompidos: {count}."),
        ("unreadable", "Há anexos sem texto legível: {count}."),
    )
    for key, template in templates:
        count = normalized[key]
        if count:
            warnings.append(template.format(count=count))
    return warnings
