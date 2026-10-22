from __future__ import annotations

import pytest

from app.scheduler import _positive, expurgar


def test_positive_accepts_safe_values() -> None:
    assert _positive(1, name="retention") == 1
    assert _positive(0.5, name="interval") == 0.5


@pytest.mark.parametrize("value", [0, -1, -0.5])
def test_positive_rejects_zero_and_negative_values(value: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        _positive(value, name="unsafe")


@pytest.mark.asyncio
async def test_expunge_rejects_non_positive_retention_before_touching_database() -> None:
    with pytest.raises(ValueError, match="retention_days"):
        await expurgar(None, retention_days=0)  # type: ignore[arg-type]
