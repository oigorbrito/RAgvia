from __future__ import annotations

import os

import pytest

from app.generation_authorization import load_generation_permit
from scripts.accept_live_summary import AcceptanceBlocked, _check_scope
from tests.integration.test_generation_authorization_postgres import (
    URL, request, setup_scope,
)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is required")


async def test_live_acceptance_preauth_gates_on_real_postgres(monkeypatch, tmp_path) -> None:
    """One actual DB lifecycle, zero external calls, no fake HTTP evidence."""
    assert URL is not None
    conn, scope = await setup_scope(monkeypatch, tmp_path)
    try:
        permit = load_generation_permit(scope)
        with pytest.raises(AcceptanceBlocked, match="judit_request_not_correlated"):
            await _check_scope(URL, scope, permit)
        identity = await conn.fetchrow(
            "SELECT p.code, v.source_request_id FROM processes p "
            "JOIN process_versions v ON v.id=$2 WHERE p.id=$1",
            scope[0], scope[1],
        )
        # A finalization flag alone does not prove Judit request/tenant linkage.
        await conn.execute(
            "INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id) "
            "VALUES ($1,$2,$3)",
            scope[2], identity["code"], identity["source_request_id"],
        )
        eligible = await _check_scope(URL, scope, permit)
        assert eligible["source_request_recorded"] is True

        await conn.execute("UPDATE processes SET secrecy_level=1 WHERE id=$1", scope[0])
        with pytest.raises(AcceptanceBlocked, match="secret_process"):
            await _check_scope(URL, scope, permit)
        await conn.execute("UPDATE processes SET secrecy_level=0 WHERE id=$1", scope[0])

        await conn.execute(
            "DELETE FROM tenant_processes WHERE tenant_id=$1 AND process_id=$2",
            scope[2], scope[0],
        )
        with pytest.raises(AcceptanceBlocked, match="tenant_or_version"):
            await _check_scope(URL, scope, permit)
        await conn.execute(
            "INSERT INTO tenant_processes(tenant_id,process_id) VALUES($1,$2)",
            scope[2], scope[0],
        )

        await conn.execute("UPDATE process_versions SET finalized=FALSE WHERE id=$1", scope[1])
        with pytest.raises(AcceptanceBlocked, match="acquisition_not_finalized"):
            await _check_scope(URL, scope, permit)
        await conn.execute("UPDATE process_versions SET finalized=TRUE WHERE id=$1", scope[1])

        # Deleting the correlation must block independently of tenant membership.
        await conn.execute(
            "DELETE FROM tenant_judit_requests WHERE tenant_id=$1 AND judit_request_id=$2",
            scope[2], identity["source_request_id"],
        )
        with pytest.raises(AcceptanceBlocked, match="judit_request_not_correlated"):
            await _check_scope(URL, scope, permit)
        await conn.execute(
            "INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id) "
            "VALUES ($1,$2,$3)",
            scope[2], identity["code"], identity["source_request_id"],
        )

        # Reserve without invoking Anthropic; the original permit is one-shot.
        await permit.reserve_message(request())
        with pytest.raises(AcceptanceBlocked, match="approval_already_consumed"):
            await _check_scope(URL, scope, permit)
    finally:
        await conn.close()
