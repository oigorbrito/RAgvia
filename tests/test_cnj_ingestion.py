import pytest

from app.judit import normalize_cnj, parse_event

CANONICAL = "0000000-00.2026.8.21.0001"
DIGITS = "00000000020268210001"


def _lawsuit(code: str) -> dict:
    return {
        "callback_id": "cb-cnj",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": "req-cnj",
        "payload": {
            "request_id": "req-cnj",
            "response_id": "resp-cnj",
            "response_type": "lawsuit",
            "response_data": {"code": code, "steps": []},
        },
    }


def test_normalize_cnj_accepts_canonical_and_digits_only() -> None:
    assert normalize_cnj(CANONICAL) == CANONICAL
    assert normalize_cnj(DIGITS) == CANONICAL


def test_parse_event_persists_canonical_cnj_shape() -> None:
    assert parse_event(_lawsuit(DIGITS)).code == CANONICAL


@pytest.mark.parametrize(
    "value",
    [
        "",
        "123",
        "0000000-00.2026.8.21.001",
        "0000000/00/2026/8/21/0001",
        "0000000-00.2026.X.21.0001",
        "000000000202682100011",
        "0000000-0020268210001",
        "000000000.2026.8.21.0001",
        "0000000-00.20268210001",
    ],
)
def test_invalid_cnj_shape_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="invalid CNJ"):
        normalize_cnj(value)
