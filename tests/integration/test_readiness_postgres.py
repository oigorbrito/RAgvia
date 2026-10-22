from __future__ import annotations

import os

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


@pytest.mark.asyncio
async def test_health_and_database_readiness_are_separate() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    app.state.pool = pool
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

        assert health.status_code == 200
        assert health.json() == {"ok": True}
        assert ready.status_code == 200
        assert ready.json() == {"ok": True}

        await pool.close()

        health_without_db = await client.get("/health")
        not_ready = await client.get("/ready")

    assert health_without_db.status_code == 200
    assert health_without_db.json() == {"ok": True}
    assert not_ready.status_code == 503
    assert not_ready.json()["detail"] == "database unavailable"
