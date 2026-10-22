from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.scheduler import acquire_singleton, expurgar, release_singleton

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)


@pytest.mark.asyncio
async def test_scheduler_advisory_lock_is_singleton() -> None:
    assert TEST_DATABASE_URL is not None
    first = await asyncpg.connect(TEST_DATABASE_URL)
    second = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        assert await acquire_singleton(first) is True
        assert await acquire_singleton(second) is False
        await release_singleton(first)
        assert await acquire_singleton(second) is True
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_expunge_removes_process_vectors_and_judit_raw_but_preserves_audit() -> None:
    assert TEST_DATABASE_URL is not None
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        tenant_id = uuid4()
        process_id = uuid4()
        version_id = uuid4()
        code = "0000000-00.0000.0.00.0099"
        request_id = "request-expunge"
        old_time = datetime.now(UTC) - timedelta(days=500)
        zero_vector = "[" + ",".join("0" for _ in range(1536)) + "]"

        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tenant_id,
            "integration",
        )
        await conn.execute(
            """
            INSERT INTO processes (id, code, updated_at)
            VALUES ($1, $2, $3)
            """,
            process_id,
            code,
            old_time,
        )
        await conn.execute(
            """
            INSERT INTO process_versions (
                id, process_id, source_request_id, source_payload,
                judit_request_id, judit_response_id, judit_callback_id
            ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            version_id,
            process_id,
            "response-expunge",
            json.dumps({"sensitive": "payload"}),
            request_id,
            "response-expunge",
            "callback-expunge",
        )
        await conn.execute(
            "UPDATE processes SET current_version_id = $2 WHERE id = $1",
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_steps (
                version_id, process_id, step_number, text, embedding
            ) VALUES ($1, $2, 1, 'sensitive movement', $3::vector)
            """,
            version_id,
            process_id,
            zero_vector,
        )
        await conn.execute(
            """
            INSERT INTO judit_deliveries (callback_id, request_id, event_type, raw_payload)
            VALUES ('callback-expunge', $1, 'response_created', $2::jsonb)
            """,
            request_id,
            json.dumps({"sensitive": "raw"}),
        )
        await conn.execute(
            """
            INSERT INTO access_log (tenant_id, process_id, process_code, action)
            VALUES ($1, $2, $3, 'read_process_summary')
            """,
            tenant_id,
            process_id,
            code,
        )

        deleted = await expurgar(conn, retention_days=365)
        assert deleted >= 1
        assert await conn.fetchval("SELECT count(*) FROM processes WHERE id = $1", process_id) == 0
        assert await conn.fetchval("SELECT count(*) FROM process_steps WHERE process_id = $1", process_id) == 0
        assert await conn.fetchval("SELECT count(*) FROM judit_deliveries WHERE request_id = $1", request_id) == 0
        audit = await conn.fetchrow(
            "SELECT process_id, process_code FROM access_log WHERE tenant_id = $1 AND process_code = $2",
            tenant_id,
            code,
        )
        assert audit is not None
        assert audit["process_id"] == process_id
        assert audit["process_code"] == code
    finally:
        await conn.close()
