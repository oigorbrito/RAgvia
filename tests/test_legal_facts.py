from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from app.claim_verification import _decimal_amount as claim_decimal_amount
from app.legal_facts import decimal_amount
from app.validation import _decimal_amount as validation_decimal_amount


def test_validation_and_claim_verification_share_exact_amount_parser() -> None:
    assert validation_decimal_amount is decimal_amount  # nosec B101
    assert claim_decimal_amount is decimal_amount  # nosec B101


@settings(max_examples=160, deadline=None)
@given(minor_units=st.integers(min_value=-999_999_999_99, max_value=999_999_999_99))
def test_equivalent_brazilian_amount_representations_parse_identically(
    minor_units: int,
) -> None:
    negative = minor_units < 0
    absolute = abs(minor_units)
    whole, cents = divmod(absolute, 100)
    sign = "-" if negative else ""
    grouped_dots = f"{whole:,}".replace(",", ".")
    grouped_spaces = f"{whole:,}".replace(",", " ")
    expected = Decimal(minor_units) / Decimal(100)

    representations = (
        f"{sign}{whole}.{cents:02d}",
        f"{sign}{whole},{cents:02d}",
        f"R$ {sign}{grouped_dots},{cents:02d}",
        f"BRL {sign}{grouped_dots},{cents:02d}",
        f"R$ {sign}{grouped_spaces},{cents:02d}",
        f"R$\u00a0{sign}{grouped_dots},{cents:02d}",
    )

    for rendered in representations:
        assert decimal_amount(rendered) == expected  # nosec B101


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "R$",
        "1.23.456,78",
        "1,234.56",
        "R$ 1.234,5x",
        "BRL --1.000,00",
        "mil reais",
    ],
)
def test_malformed_or_non_numeric_amounts_are_not_guessed(value: object) -> None:
    assert decimal_amount(value) is None  # nosec B101


def test_native_numeric_values_preserve_existing_decimal_semantics() -> None:
    assert decimal_amount(1234) == Decimal("1234")  # nosec B101
    assert decimal_amount(1234.5) == Decimal("1234.5")  # nosec B101
    assert decimal_amount(Decimal("1234.50")) == Decimal("1234.50")  # nosec B101
