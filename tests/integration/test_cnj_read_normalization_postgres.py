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


def _canonical_code() -> str:
    prefix = uuid4().int % 10_000_000
    suffix = uuid4().int % 10_000
    return f"{prefix:07d}-00.2026.8.21.{suffix:04d}"


@pytest.mark.asyncio
async def test_digit_only_process_read_uses_canonical_identity_and_audit() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    tenant_id = uuid4()
    process_id = uuid4()
    token = f"token-{uuid4()}"
    canonical = _canonical_code()
    digits = "".join(character for character in canonical if character.isdigit())

    app.state.pool = pool
    app.state.bearer_tokens = {token: tenant_id}
    transport = httpx.ASGITransport(app=app)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'cnj-read-test')",
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO processes (id, code, class_name, court) VALUES ($1, $2, 'Classe', 'TJ')",
                process_id,
                canonical,
            )
            await conn.execute(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
                tenant_id,
                process_id,
            )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/processes/{digits}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert response.json()["code"] == canonical

            invalid = await client.get(
                "/processes/not-a-cnj",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert invalid.status_code == 400

        async with pool.acquire() as conn:
            audit_codes = await conn.fetch(
                """
                SELECT process_code
                FROM access_log
                WHERE tenant_id = $1
                  AND process_id = $2
                  AND action = 'read_process_summary'
                """,
                tenant_id,
                process_id,
            )
            assert [row["process_code"] for row in audit_codes] == [canonical]
    finally:
        # access_log is intentionally immutable and retains the tenant reference.
        # Keep the randomized tenant row rather than weakening the audit invariant
        # merely to clean up integration-test data.
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tenant_processes WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM processes WHERE id = $1", process_id)
        await pool.close()
