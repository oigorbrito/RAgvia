"""O bootstrap real não pode renovar nem duplicar o tenant em reaberturas."""
import os
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from scripts.bootstrap_homologation import prepare_tenant

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")


@pytest.mark.asyncio
async def test_homologation_tenant_bootstrap_is_durably_idempotent(monkeypatch):
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    tenant = uuid4()
    first, code = await prepare_tenant(tenant)
    assert code == 0 and first["tenant_exists"]
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        before = await conn.fetchrow("SELECT * FROM tenants WHERE id=$1", tenant)
        second, code = await prepare_tenant(tenant)
        after = await conn.fetchrow("SELECT * FROM tenants WHERE id=$1", tenant)
        assert code == 0 and second == first
        assert dict(before) == dict(after)
    finally:
        await conn.execute("DELETE FROM tenants WHERE id=$1", tenant)
        await conn.close()
