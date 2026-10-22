from __future__ import annotations

import os

import httpx
import pytest

from app.api import app
from app.api_key_auth import api_key_hash_and_fingerprint
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

CODE_ALLOWED = "0000000-00.2026.8.21.1475"
CODE_DENIED = "0000000-00.2026.8.21.1476"
TOKEN = "sk_test_tracking_scope_0000000001"


@pytest.mark.asyncio
async def test_api_key_tracking_routes_respect_cnj_scope(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("RPY_API_KEY_ENVIRONMENT", "test")
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    app.state.bearer_tokens = {}
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE api_key_rate_limits, api_key_cnj_scopes, api_keys,
                         judit_tracking_refreshes, judit_trackings,
                         public_summary_requests, jobs, judit_deliveries,
                         judit_request_completions, process_summaries, process_steps,
                         tenant_processes, tenant_judit_requests, access_log,
                         process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('tracking api-key tenant') RETURNING id"
            )
            key_hash, fingerprint = api_key_hash_and_fingerprint(TOKEN)
            key_id = await conn.fetchval(
                """
                INSERT INTO api_keys (
                    tenant_id, name, key_hash, fingerprint, environment,
                    allow_portfolio, rate_limit_per_minute
                )
                VALUES ($1, 'tracking-scope', $2, $3, 'test', FALSE, 100)
                RETURNING id
                """,
                tenant_id,
                key_hash,
                fingerprint,
            )
            await conn.execute(
                "INSERT INTO api_key_cnj_scopes (api_key_id, process_code) VALUES ($1,$2)",
                key_id,
                CODE_ALLOWED,
            )
            denied_tracking_id = await conn.fetchval(
                """
                INSERT INTO judit_trackings (
                    tenant_id, process_code, provider_tracking_id, status, recurrence_days
                )
                VALUES ($1, $2, 'provider-denied', 'active', 1)
                RETURNING id
                """,
                tenant_id,
                CODE_DENIED,
            )

        headers = {"Authorization": f"Bearer {TOKEN}"}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            allowed = await client.post(
                f"/v1/trackings/{CODE_ALLOWED}",
                headers=headers,
            )
            denied = await client.post(
                f"/v1/trackings/{CODE_DENIED}",
                headers=headers,
            )
            listed = await client.get("/v1/trackings", headers=headers)
            delete_denied = await client.delete(
                f"/v1/trackings/{denied_tracking_id}",
                headers=headers,
            )

        assert allowed.status_code == 202
        assert denied.status_code == 404
        assert delete_denied.status_code == 404
        assert [item["code"] for item in listed.json()["trackings"]] == [CODE_ALLOWED]
    finally:
        await pool.close()
