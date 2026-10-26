from __future__ import annotations

import json
import os
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from scripts.import_judit_get_snapshot import (
    SnapshotRejected,
    stage_snapshot,
    validate_snapshot,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL required"
)


def _code() -> str:
    prefix = f"{uuid4().int % 10**7:07d}"
    rest = "20268160021"
    check = 98 - (int(prefix + rest + "00") % 97)
    return f"{prefix}-{check:02d}.2026.8.16.0021"


def _snapshot(code: str) -> bytes:
    request_id = str(uuid4())
    pages = [
        {
            "request_id": request_id,
            "response_id": str(uuid4()),
            "response_type": "lawsuit",
            "cached": False,
            "response_data": {
                "code": code, "secrecy_level": 0,
                "parties": [{"name": "Pessoa Ficticia", "main_document": "12345678901"}],
                "steps": [{"step_id": "step-1", "private": False,
                           "content": "Distribuicao de autos."}],
                "attachments": [],
            },
        },
        {
            "request_id": request_id, "response_id": str(uuid4()),
            "response_type": "summary",
            "response_data": {"data": ["EXTERNAL_SUMMARY_NOT_FOR_INGESTION"]},
        },
    ]
    return json.dumps({
        "request_status": "completed", "page": 1, "page_count": 2,
        "all_pages_count": 1, "all_count": 2, "page_data": pages,
    }).encode()


@pytest.mark.asyncio
async def test_get_snapshot_postgres_stages_once_with_no_webhooks_or_generation() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    tenant_id = uuid4()
    cnj = _code()
    captured = _snapshot(cnj)
    snapshot = validate_snapshot(captured, expected_cnj=cnj)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("INSERT INTO tenants(id,name) VALUES ($1,'LIVE07 Synthetic')", tenant_id)
        assert await stage_snapshot(
            snapshot, database_url=TEST_DATABASE_URL, tenant_id=tenant_id
        ) == "staged"
        assert await stage_snapshot(
            snapshot, database_url=TEST_DATABASE_URL, tenant_id=tenant_id
        ) == "already_staged_same_capture"
        version = await conn.fetchrow(
            """
            SELECT pv.id, pv.finalized, pv.source_payload, pv.judit_callback_id,
                   p.current_version_id
            FROM process_versions pv
            JOIN processes p ON p.id=pv.process_id
            WHERE pv.judit_response_id=$1
            """,
            snapshot.response_id,
        )
        assert version is not None
        assert version["finalized"] is False
        assert version["current_version_id"] is None
        assert version["judit_callback_id"] is None
        payload = json.dumps(version["source_payload"])
        assert "EXTERNAL_SUMMARY_NOT_FOR_INGESTION" not in payload
        assert '"webhook_authenticated": false' in payload
        assert await conn.fetchval(
            "SELECT count(*) FROM process_versions WHERE judit_request_id=$1",
            snapshot.request_id,
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM tenant_judit_requests WHERE judit_request_id=$1",
            snapshot.request_id,
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM judit_deliveries WHERE request_id=$1", snapshot.request_id
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM judit_request_completions WHERE request_id=$1",
            snapshot.request_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name IN ('finalize_judit_request','generate_process_summary') AND payload::text LIKE $1",
            "%" + snapshot.request_id + "%",
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM access_log WHERE tenant_id=$1 AND action='import_judit_get_snapshot'",
            tenant_id,
        ) == 1
        changed = json.loads(captured)
        changed["page_data"][0]["response_data"]["steps"][0]["content"] = "Modified"
        altered = validate_snapshot(json.dumps(changed).encode(), expected_cnj=cnj)
        with pytest.raises(SnapshotRejected, match="response_already_owned_by_other_source"):
            await stage_snapshot(
                altered, database_url=TEST_DATABASE_URL, tenant_id=tenant_id
            )
    finally:
        await conn.execute("DELETE FROM processes WHERE code=$1", cnj)
        await conn.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
        await conn.close()
