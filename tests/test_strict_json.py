from __future__ import annotations

import pytest

from app.json_utils import decode_json_value, loads_strict_json


def test_strict_json_accepts_nested_standard_json() -> None:
    parsed = loads_strict_json(
        b'{"outer":{"items":[1,2,3],"enabled":true},"name":"ok"}'
    )
    if parsed != {
        "outer": {"items": [1, 2, 3], "enabled": True},
        "name": "ok",
    }:
        raise AssertionError(f"unexpected strict JSON parse result: {parsed!r}")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"field":"first","field":"second"}',
        b'{"outer":{"id":1,"id":2}}',
    ],
)
def test_strict_json_rejects_duplicate_object_keys(payload: bytes) -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        loads_strict_json(payload)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_non_standard_numeric_constants(constant: str) -> None:
    with pytest.raises(ValueError, match="non-standard numeric constant"):
        loads_strict_json(f'{{"value":{constant}}}')


@pytest.mark.parametrize(
    "payload",
    [
        '{"field":"first","field":"second"}',
        '{"value":NaN}',
    ],
)
def test_shared_json_decoder_uses_strict_semantics(payload: str) -> None:
    with pytest.raises(ValueError):
        decode_json_value(payload)


def test_strict_json_rejects_pathological_nesting_as_value_error(
    parser_rejected_json_depth: int,
) -> None:
    depth = parser_rejected_json_depth
    payload = "[" * depth + "0" + "]" * depth
    with pytest.raises(ValueError, match="nesting exceeds safe parser depth"):
        loads_strict_json(payload)


@pytest.mark.parametrize("representation", [str, bytes, bytearray])
@pytest.mark.parametrize("container", ["array", "object"])
def test_strict_json_depth_failure_preserves_input_contract(
    representation, container, parser_rejected_json_depth: int,
) -> None:
    depth = parser_rejected_json_depth
    if container == "array":
        payload = "[" * depth + "0" + "]" * depth
    else:
        payload = '{"nested":' * depth + "0" + "}" * depth
    value = payload if representation is str else representation(payload, "utf-8")
    with pytest.raises(ValueError, match="nesting exceeds safe parser depth"):
        loads_strict_json(value)


def test_shared_json_decoder_normalizes_excessive_nesting(
    parser_rejected_json_depth: int,
) -> None:
    depth = parser_rejected_json_depth
    payload = "[" * depth + "0" + "]" * depth
    with pytest.raises(ValueError, match="nesting exceeds safe parser depth"):
        decode_json_value(payload)
