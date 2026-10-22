from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.migrations import migrate
from app.scheduler import expurgar

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_expunge_cascades_versions_summaries_tenant_links_and_vectors_but_keeps_audit() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        tenant_id = uuid4()
        process_id = uuid4()
        version_id = uuid4()
        code = "0000000-00.0000.0.00.0401"
        request_id = f"request-expunge-{uuid4()}"
        callback_id = f"callback-expunge-{uuid4()}"
        old_time = datetime.now(UTC) - timedelta(days=500)
        zero_vector = "[" + ",".join("0" for _ in range(1536)) + "]"

        await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'expunge tenant')", tenant_id)
        await conn.execute(
            """
            INSERT INTO processes (
                id, code, court, class_name, subjects, parties, header, updated_at
            ) VALUES ($1, $2, 'TJRS', 'Classe Sensível', $3::jsonb, $4::jsonb, $5::jsonb, $6)
            """,
            process_id,
            code,
            json.dumps([{"name": "assunto sensível"}]),
            json.dumps([{"name": "Pessoa Sensível"}]),
            json.dumps({"sensitive": "header"}),
            old_time,
        )
        await conn.execute(
            """
            INSERT INTO process_versions (
                id, process_id, source_request_id, source_payload,
                judit_request_id, judit_response_id, judit_callback_id, finalized
            ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, TRUE)
            """,
            version_id,
            process_id,
            "source-expunge",
            json.dumps({"sensitive": "raw lawsuit payload"}),
            request_id,
            "response-expunge",
            callback_id,
        )
        await conn.execute("UPDATE processes SET current_version_id = $2 WHERE id = $1", process_id, version_id)
        await conn.execute(
            "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
            tenant_id,
            process_id,
        )
        await conn.execute(
            """
            INSERT INTO process_steps (
                version_id, process_id, step_number, title, text, metadata, embedding
            ) VALUES ($1, $2, 1, 'SENTENÇA', 'movimento sensível', $3::jsonb, $4::vector)
            """,
            version_id,
            process_id,
            json.dumps({"sensitive": "step metadata"}),
            zero_vector,
        )
        await conn.execute(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, 'resumo sensível', $3::jsonb, 'claude-sonnet-5', 'test')
            """,
            process_id,
            version_id,
            json.dumps({"passed": True}),
        )
        await conn.execute(
            """
            INSERT INTO judit_deliveries (callback_id, request_id, event_type, raw_payload)
            VALUES ($1, $2, 'response_created', $3::jsonb)
            """,
            callback_id,
            request_id,
            json.dumps({"sensitive": "delivery raw"}),
        )
        await conn.execute(
            """
            INSERT INTO access_log (tenant_id, process_id, process_code, action, metadata)
            VALUES ($1, $2, $3, 'read_process_summary', $4::jsonb)
            """,
            tenant_id,
            process_id,
            code,
            json.dumps({"audit": "preserve"}),
        )

        assert await expurgar(conn, retention_days=365) >= 1

        for table, column in (
            ("processes", "id"),
            ("process_versions", "process_id"),
            ("process_steps", "process_id"),
            ("process_summaries", "process_id"),
            ("tenant_processes", "process_id"),
        ):
            assert await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE {column} = $1",
                process_id,
            ) == 0

        assert await conn.fetchval(
            "SELECT count(*) FROM judit_deliveries WHERE request_id = $1",
            request_id,
        ) == 0

        audit = await conn.fetchrow(
            """
            SELECT process_id, process_code, metadata
            FROM access_log
            WHERE tenant_id = $1 AND process_code = $2
            """,
            tenant_id,
            code,
        )
        assert audit is not None
        assert audit["process_id"] == process_id
        assert audit["process_code"] == code
        assert "preserve" in str(audit["metadata"])
        assert await conn.fetchval("SELECT count(*) FROM tenants WHERE id = $1", tenant_id) == 1
    finally:
        await conn.close()
