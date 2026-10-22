from __future__ import annotations

import json
from typing import Any


def decode_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return loads_strict_json(value)
    return value


def decode_json_object(value: Any, *, label: str = "JSON value") -> dict[str, Any]:
    if value is None:
        return {}
    decoded = decode_json_value(value)
    if isinstance(decoded, dict):
        return dict(decoded)
    try:
        return dict(decoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a JSON object") from exc


def decode_json_list(value: Any, *, label: str = "JSON value") -> list[Any]:
    if value is None:
        return []
    decoded = decode_json_value(value)
    if isinstance(decoded, list):
        return list(decoded)
    if isinstance(decoded, tuple):
        return list(decoded)
    raise ValueError(f"{label} must be a JSON array")


def _reject_duplicate_json_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains duplicate key")
        result[key] = value
    return result


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(f"JSON contains non-standard numeric constant: {value}")


def loads_strict_json(value: str | bytes | bytearray) -> Any:
    try:
        return json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_object_pairs,
            parse_constant=_reject_non_standard_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds safe parser depth") from exc
