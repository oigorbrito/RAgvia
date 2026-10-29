from __future__ import annotations

from typing import Any


class PermanentTaskError(Exception):
    """Deterministic task failure that must not be retried by the durable queue."""


async def healthcheck(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "payload": payload}
