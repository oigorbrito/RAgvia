from __future__ import annotations

import httpx
import pytest
from uuid import uuid4

from app.api import app


@pytest.mark.asyncio
async def test_judit_webhook_rejects_duplicate_json_keys_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = uuid4().hex
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", token)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/webhooks/judit/{token}",
            headers={"Content-Type": "application/json"},
            content=(
                '{"event_type":"request_completed",'
                '"event_type":"response_created",'
                '"reference_type":"request","reference_id":"req-1",'
                '"payload":{"status":"completed"}}'
            ),
        )

    if response.status_code != 400:
        raise AssertionError(
            f"expected duplicate webhook JSON to return 400, got {response.status_code}"
        )
    if response.json().get("detail") != "invalid payload":
        raise AssertionError(f"unexpected webhook error response: {response.json()!r}")


@pytest.mark.asyncio
async def test_judit_webhook_rejects_excessive_nesting_before_database(
    monkeypatch: pytest.MonkeyPatch, parser_rejected_json_depth: int,
) -> None:
    depth = parser_rejected_json_depth
    token = uuid4().hex
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", token)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/webhooks/judit/{token}",
            headers={"Content-Type": "application/json"},
            content='{"unused":' + "[" * depth + "0" + "]" * depth + "}",
        )
    if response.status_code != 400:
        raise AssertionError("Excessively nested webhook JSON did not return 400")
    if response.json().get("detail") != "invalid payload":
        raise AssertionError("Nested webhook JSON changed the public error contract")
