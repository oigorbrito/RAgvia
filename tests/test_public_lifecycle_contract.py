from __future__ import annotations

import pytest

from app.public_lifecycle import request_fingerprint


def test_request_fingerprint_is_stable_for_equivalent_json_objects() -> None:
    left = request_fingerprint(
        {"cnj": "0000000-00.0000.0.00.0000", "format": "json", "force": True}
    )
    right = request_fingerprint(
        {"force": True, "format": "json", "cnj": "0000000-00.0000.0.00.0000"}
    )
    if left != right:
        raise AssertionError("canonical idempotency fingerprint changed with key order")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_request_fingerprint_rejects_non_finite_json_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        request_fingerprint(
            {
                "cnj": "0000000-00.0000.0.00.0000",
                "format": "json",
                "unused": {"score": value},
            }
        )
