from __future__ import annotations

import pytest

from app.scheduler import _positive


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_scheduler_positive_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError, match="TRACKING_STALE_HOURS must be finite"):
        _positive(value, name="TRACKING_STALE_HOURS")


def test_scheduler_positive_preserves_positive_values() -> None:
    assert _positive(36.0, name="TRACKING_STALE_HOURS") == 36.0  # nosec B101


def test_scheduler_positive_preserves_positive_integer() -> None:
    assert _positive(10, name="RETENTION_DAYS") == 10  # nosec B101


@pytest.mark.parametrize("value", [0, -1, -0.5])
def test_scheduler_positive_still_rejects_nonpositive_values(value: int | float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        _positive(value, name="RETENTION_DAYS")
