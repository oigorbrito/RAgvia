from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api_v1 import _summary_source
from app.public_lifecycle import request_fingerprint


def test_summary_source_defaults_to_judit() -> None:
    assert _summary_source(None) == "judit"
    assert _summary_source("JUDIT") == "judit"


@pytest.mark.parametrize("value", ["datajud", "unknown", 123])
def test_summary_source_rejects_non_judit_values(value) -> None:
    with pytest.raises(HTTPException) as exc:
        _summary_source(value)
    assert exc.value.status_code == 400
    assert exc.value.detail == "source must be 'judit'"


def test_summary_source_is_part_of_idempotency_fingerprint() -> None:
    base = {"cnj": "0000000-00.2026.8.21.0001", "format": "json"}
    judit = {**base, "source": "judit"}
    datajud = {**base, "source": "datajud"}
    assert request_fingerprint(judit) != request_fingerprint(datajud)
