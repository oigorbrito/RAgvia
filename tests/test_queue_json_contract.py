from __future__ import annotations

from uuid import uuid4

import pytest

from app.queue import _strict_json_dumps, complete, enqueue


class _ForbiddenConnection:
    async def fetchrow(self, *_args, **_kwargs):
        raise AssertionError("database must not be touched for invalid queue JSON")


@pytest.mark.parametrize(
    "value",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
    ],
)
def test_strict_queue_json_rejects_non_finite_numbers(value) -> None:
    with pytest.raises(ValueError, match="must be strict JSON"):
        _strict_json_dumps(value, label="queue value")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"value": float("nan")},
        {"nested": {"value": float("inf")}},
    ],
)
async def test_enqueue_rejects_invalid_json_before_database(payload) -> None:
    with pytest.raises(ValueError, match="job payload must be strict JSON"):
        await enqueue(
            _ForbiddenConnection(),  # type: ignore[arg-type]
            task_name="synthetic",
            payload=payload,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"value": float("nan")},
        {"nested": [1, float("-inf")]},
    ],
)
async def test_complete_rejects_invalid_json_before_database(result) -> None:
    with pytest.raises(ValueError, match="job result must be strict JSON"):
        await complete(
            _ForbiddenConnection(),  # type: ignore[arg-type]
            uuid4(),
            uuid4(),
            result,
        )
