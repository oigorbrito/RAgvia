from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from app.generation_authorization import load_generation_permit, PROPOSED_MODEL
from app.migrations import migrate
from app.providers import anthropic_client
from app.tasks import PermanentTaskError
from tests.test_generation_authorization import configure_approval

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")


def request():
    return {"model": PROPOSED_MODEL, "max_tokens": 4000,
            "system": [{"type": "text", "text": "Synthetic only"}],
            "messages": [{"role": "user", "content": "Synthetic only"}],
            "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}}}


async def setup_scope(monkeypatch, tmp_path):
    assert URL
    await migrate(URL)
    conn = await asyncpg.connect(URL)
    process_id, version_id, tenant_id = uuid4(), uuid4(), uuid4()
    await conn.execute("INSERT INTO tenants(id,name) VALUES($1,'Synthetic financial approval')", tenant_id)
    # CNJ is structural, never a real identifier.
    serial = f"{process_id.int % 100_000_000_000:011d}"
    code = f"{serial[:7]}-00.0000.0.00.{serial[7:]}"
    await conn.execute("INSERT INTO processes(id,code) VALUES($1,$2)", process_id, code)
    await conn.execute("INSERT INTO process_versions(id,process_id,source_request_id,finalized) VALUES($1,$2,$3,TRUE)", version_id, process_id, str(version_id))
    await conn.execute("UPDATE processes SET current_version_id=$2 WHERE id=$1", process_id, version_id)
    await conn.execute("INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)", tenant_id, process_id)
    configure_approval(monkeypatch, tmp_path, (process_id, version_id, tenant_id), approval_id="APR-" + uuid4().hex.upper())
    monkeypatch.setenv("DATABASE_URL", URL)
    return conn, (process_id, version_id, tenant_id)


async def test_concurrent_claim_retry_restart_and_expunge_do_not_renew_budget(monkeypatch, tmp_path):
    conn, scope = await setup_scope(monkeypatch, tmp_path)
    try:
        permits = [load_generation_permit(scope), load_generation_permit(scope)]
        results = await asyncio.gather(*(p.reserve_message(request()) for p in permits), return_exceptions=True)
        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, PermanentTaskError) for result in results) == 1
        winner = permits[results.index(None)]
        await winner.reserve_message(request())
        with pytest.raises(PermanentTaskError):
            await winner.reserve_message(request())
        with pytest.raises(PermanentTaskError):
            await load_generation_permit(scope).reserve_message(request())
        with pytest.raises(asyncpg.RaiseError, match="renewed or refunded"):
            await conn.execute("UPDATE anthropic_generation_reservations SET messages_reserved=1 WHERE approval_id=$1", winner.approval_id)
        await conn.execute("DELETE FROM processes WHERE id=$1", scope[0])
        assert await conn.fetchval("SELECT messages_reserved FROM anthropic_generation_reservations WHERE approval_id=$1", winner.approval_id) == 2
    finally:
        await conn.close()


async def test_timeout_consumes_approval_before_network_and_restart_is_blocked(monkeypatch, tmp_path):
    conn, scope = await setup_scope(monkeypatch, tmp_path)
    calls = []
    from app import providers
    from types import SimpleNamespace
    async def network(**kwargs):
        calls.append(1)
        raise TimeoutError("synthetic lost response")
    async def close():
        pass
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=network), close=close))
    try:
        client = anthropic_client("synthetic-only", authorization_scope=scope)
        with pytest.raises(TimeoutError):
            await client.messages.create(**request())
        retried = anthropic_client("synthetic-only", authorization_scope=scope)
        with pytest.raises(PermanentTaskError):
            await retried.messages.create(**request())
        assert calls == [1]
        assert await conn.fetchval("SELECT count(*) FROM anthropic_generation_reservations WHERE process_id=$1", scope[0]) == 1
    finally:
        await conn.close()


async def test_secret_tenant_and_bad_request_never_call_sdk(monkeypatch, tmp_path):
    conn, scope = await setup_scope(monkeypatch, tmp_path)
    calls = []
    from app import providers
    from types import SimpleNamespace
    async def network(**kwargs):
        calls.append(kwargs)
        raise AssertionError("blocked request reached network boundary")
    async def close():
        pass
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=network), close=close))
    try:
        # Fully valid document is still insufficient if tenant permission is lost.
        client = anthropic_client("synthetic-only", authorization_scope=scope)
        await conn.execute("DELETE FROM tenant_processes WHERE tenant_id=$1 AND process_id=$2", scope[2], scope[0])
        with pytest.raises(PermanentTaskError):
            await client.messages.create(**request())
        await conn.execute("INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)", scope[2], scope[0])
        await conn.execute("UPDATE processes SET secrecy_level=1 WHERE id=$1", scope[0])
        with pytest.raises(PermanentTaskError):
            await client.messages.create(**request())
        bad_request = dict(request(), tools=[])
        with pytest.raises(PermanentTaskError):
            await client.messages.create(**bad_request)
        assert calls == []
    finally:
        await conn.close()
