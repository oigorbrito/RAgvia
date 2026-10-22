import pytest

from app.tasks import resolve_task, task


def test_resolve_unknown_task_raises() -> None:
    with pytest.raises(LookupError, match="unknown task"):
        resolve_task("does-not-exist")


def test_duplicate_task_registration_fails() -> None:
    @task("unit-test-task")
    async def first(payload):
        return payload

    with pytest.raises(RuntimeError, match="already registered"):
        @task("unit-test-task")
        async def second(payload):
            return payload
