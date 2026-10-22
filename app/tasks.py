from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

TaskHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]

_TASKS: dict[str, TaskHandler] = {}


class PermanentTaskError(Exception):
    """Deterministic task failure that must not be retried by the durable queue."""


def task(name: str) -> Callable[[TaskHandler], TaskHandler]:
    def register(handler: TaskHandler) -> TaskHandler:
        if name in _TASKS:
            raise RuntimeError(f"task already registered: {name}")
        _TASKS[name] = handler
        return handler

    return register


def resolve_task(name: str) -> TaskHandler:
    try:
        return _TASKS[name]
    except KeyError as exc:
        raise LookupError(f"unknown task: {name}") from exc


@task("healthcheck")
async def healthcheck(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "payload": payload}
