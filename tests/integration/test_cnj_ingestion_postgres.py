from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

CANONICAL = "0000000-00.2026.8.21.0001"
DIGITS = "00000000020268210001"


@pytest.fixture
async def api_client(monkeypatch: pytest.MonkeyPatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "integration-webhook")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, pool
    await pool.close()


def _body(*, code: str, request_id: str, response_id: str, callback_id: str) -> dict:
    return {
        "callback_id": callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {"code": code, "steps": []},
            "tags": {"cached_response": False},
        },
    }


@pytest.mark.asyncio
async def test_different_cnj_formatting_maps_to_one_process(api_client) -> None:
    client, pool = api_client
    request_a = f"req-{uuid4()}"
    request_b = f"req-{uuid4()}"

    first = await client.post(
        "/webhooks/judit/integration-webhook",
        json=_body(
            code=DIGITS,
            request_id=request_a,
            response_id=f"resp-{uuid4()}",
            callback_id=f"cb-{uuid4()}",
        ),
    )
    second = await client.post(
        "/webhooks/judit/integration-webhook",
        json=_body(
            code=CANONICAL,
            request_id=request_b,
            response_id=f"resp-{uuid4()}",
            callback_id=f"cb-{uuid4()}",
        ),
    )
    assert first.status_code == 200
    assert second.status_code == 200

    async with pool.acquire() as conn:
        process_count = await conn.fetchval(
            "SELECT count(*) FROM processes WHERE code = $1",
            CANONICAL,
        )
        version_count = await conn.fetchval(
            """
            SELECT count(*)
            FROM process_versions pv
            JOIN processes p ON p.id = pv.process_id
            WHERE p.code = $1
              AND pv.judit_request_id = ANY($2::text[])
            """,
            CANONICAL,
            [request_a, request_b],
        )

    assert process_count == 1
    assert version_count == 2
