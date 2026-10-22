from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

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


async def _insert_key(
    conn,
    *,
    tenant_id,
    token: str,
    name: str,
    allow_portfolio: bool = False,
    rate_limit: int = 60,
    scopes: tuple[str, ...] = (),
    revoked: bool = False,
    expires_at=None,
):
    key_hash, fingerprint = api_key_hash_and_fingerprint(token)
    environment = "live" if token.startswith("sk_live_") else "test"
    key_id = await conn.fetchval(
        """
        INSERT INTO api_keys (
            tenant_id, name, key_hash, fingerprint, environment,
            allow_portfolio, rate_limit_per_minute, expires_at, revoked_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING id
        """,
        tenant_id,
        name,
        key_hash,
        fingerprint,
        environment,
        allow_portfolio,
        rate_limit,
        expires_at,
        datetime.now(timezone.utc) if revoked else None,
    )
    for code in scopes:
        await conn.execute(
            "INSERT INTO api_key_cnj_scopes (api_key_id, process_code) VALUES ($1,$2)",
            key_id,
            code,
        )
    return key_id, fingerprint


@pytest.mark.asyncio
async def test_api_keys_enforce_scope_rate_limit_lifecycle_and_safe_audit(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("RPY_API_KEY_ENVIRONMENT", "test")
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    app.state.bearer_tokens = {}

    code_portfolio = "0000000-00.2026.8.21.0201"
    code_explicit = "0000000-00.2026.8.21.0202"
    code_forbidden = "0000000-00.2026.8.21.0203"
    code_other_tenant = "0000000-00.2026.8.21.0204"
    token_scoped = "sk_test_scoped_key_material_00000001"
    token_portfolio = "sk_test_portfolio_key_material_00000002"
    token_request = "sk_test_request_key_material_00000003"
    token_denied = "sk_test_denied_key_material_00000004"
    token_revoked = "sk_test_revoked_key_material_00000005"
    token_expired = "sk_test_expired_key_material_00000006"

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE api_key_rate_limits, api_key_cnj_scopes, api_keys,
                         jobs, tenant_judit_requests, judit_request_completions,
                         judit_deliveries, process_summaries, process_steps,
                         tenant_processes, access_log, process_versions,
                         processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            tenant_a = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('Tenant API A') RETURNING id"
            )
            tenant_b = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('Tenant API B') RETURNING id"
            )
            process_a = await conn.fetchval(
                "INSERT INTO processes (code, header) VALUES ($1, '{}'::jsonb) RETURNING id",
                code_portfolio,
            )
            process_b = await conn.fetchval(
                "INSERT INTO processes (code, header) VALUES ($1, '{}'::jsonb) RETURNING id",
                code_other_tenant,
            )
            await conn.execute(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1,$2),($3,$4)",
                tenant_a,
                process_a,
                tenant_b,
                process_b,
            )

            scoped_id, scoped_fingerprint = await _insert_key(
                conn,
                tenant_id=tenant_a,
                token=token_scoped,
                name="scoped",
                rate_limit=2,
                scopes=(code_portfolio,),
            )
            await _insert_key(
                conn,
                tenant_id=tenant_a,
                token=token_portfolio,
                name="portfolio",
                allow_portfolio=True,
            )
            await _insert_key(
                conn,
                tenant_id=tenant_a,
                token=token_request,
                name="request",
                scopes=(code_explicit,),
            )
            denied_id, denied_fingerprint = await _insert_key(
                conn,
                tenant_id=tenant_a,
                token=token_denied,
                name="denied",
            )
            await _insert_key(
                conn,
                tenant_id=tenant_a,
                token=token_revoked,
                name="revoked",
                scopes=(code_portfolio,),
                revoked=True,
            )
            await _insert_key(
                conn,
                tenant_id=tenant_a,
                token=token_expired,
                name="expired",
                scopes=(code_portfolio,),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {token_scoped}"}
            first = await client.get(f"/processes/{code_portfolio}", headers=headers)
            assert first.status_code == 200
            assert first.headers["X-RateLimit-Remaining"] == "1"

            second = await client.get(f"/processes/{code_portfolio}", headers=headers)
            assert second.status_code == 200
            assert second.headers["X-RateLimit-Remaining"] == "0"

            limited = await client.get(f"/processes/{code_portfolio}", headers=headers)
            assert limited.status_code == 429
            assert limited.headers["X-RateLimit-Remaining"] == "0"
            assert int(limited.headers["Retry-After"]) >= 1

            portfolio = await client.get(
                f"/processes/{code_portfolio}",
                headers={"Authorization": f"Bearer {token_portfolio}"},
            )
            assert portfolio.status_code == 200

            cross_tenant = await client.get(
                f"/processes/{code_other_tenant}",
                headers={"Authorization": f"Bearer {token_portfolio}"},
            )
            assert cross_tenant.status_code == 404

            requested = await client.post(
                f"/processes/{code_explicit}/request",
                headers={"Authorization": f"Bearer {token_request}"},
            )
            assert requested.status_code == 202

            denied = await client.post(
                f"/processes/{code_forbidden}/request",
                headers={"Authorization": f"Bearer {token_denied}"},
            )
            assert denied.status_code == 404

            revoked = await client.get(
                f"/processes/{code_portfolio}",
                headers={"Authorization": f"Bearer {token_revoked}"},
            )
            assert revoked.status_code == 401

            expired = await client.get(
                f"/processes/{code_portfolio}",
                headers={"Authorization": f"Bearer {token_expired}"},
            )
            assert expired.status_code == 401

        async with pool.acquire() as conn:
            acquisition = await conn.fetchrow(
                "SELECT tenant_id, process_code FROM tenant_judit_requests WHERE process_code=$1",
                code_explicit,
            )
            assert acquisition is not None
            assert acquisition["tenant_id"] == tenant_a
            assert await conn.fetchval(
                "SELECT count(*) FROM tenant_judit_requests WHERE process_code=$1",
                code_forbidden,
            ) == 0
            assert await conn.fetchval(
                """
                SELECT count(*) FROM jobs
                WHERE task_name='request_judit_process'
                  AND payload->>'tenant_request_id' IN (
                      SELECT id::text FROM tenant_judit_requests WHERE process_code=$1
                  )
                """,
                code_forbidden,
            ) == 0

            audit_rows = await conn.fetch(
                """
                SELECT process_code, action, api_key_id, api_key_fingerprint, metadata
                FROM access_log
                WHERE api_key_id IS NOT NULL
                ORDER BY id
                """
            )
            assert audit_rows
            assert any(
                row["api_key_id"] == scoped_id
                and row["api_key_fingerprint"] == scoped_fingerprint
                and row["process_code"] == code_portfolio
                for row in audit_rows
            )
            assert any(
                row["api_key_id"] == denied_id
                and row["api_key_fingerprint"] == denied_fingerprint
                and row["action"] == "authorization_denied"
                and row["process_code"] == code_forbidden
                for row in audit_rows
            )
            rendered_audit = json.dumps(
                [dict(row) for row in audit_rows], ensure_ascii=False, default=str
            )
            for secret in (
                token_scoped,
                token_portfolio,
                token_request,
                token_denied,
                token_revoked,
                token_expired,
            ):
                assert secret not in rendered_audit

            assert await conn.fetchval(
                "SELECT request_count FROM api_key_rate_limits WHERE api_key_id=$1",
                scoped_id,
            ) == 2
    finally:
        await pool.close()
