from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.api import _record_delivery
from app.db import create_pool
from app.judit import parse_event
from app.migrations import migrate
from app.processes import finalize_version, log_access, stage_version
from app.queue import claim, complete, enqueue

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_jsonb_columns_remain_native_objects_and_arrays() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    tenant_id = uuid4()
    worker_id = uuid4()
    code = "0000000-00.0000.0.00.0401"

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                         tenant_processes, access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'jsonb test')",
                tenant_id,
            )

            process_id, version_id = await stage_version(
                conn,
                code=code,
                source_request_id="jsonb-source",
                cached_response=False,
                payload={"event": "response_created", "nested": {"ok": True}},
            )
            promoted = await finalize_version(
                conn,
                process_id=process_id,
                version_id=version_id,
                header={"court": "TJRS"},
                parties=[{"name": "Parte Teste"}],
                subjects=[{"name": "Assunto"}],
                steps=[
                    {
                        "step_number": 1,
                        "occurred_at": None,
                        "title": "MOVIMENTO",
                        "text": "conteúdo",
                        "metadata": {"kind": "test"},
                    }
                ],
                court="TJRS",
                class_name="Classe",
            )
            assert promoted is True

            await log_access(
                conn,
                tenant_id=tenant_id,
                process_id=process_id,
                process_code=code,
                action="jsonb_contract",
                metadata={"origin": "integration"},
            )

            job = await enqueue(
                conn,
                task_name="jsonb_task",
                payload={"process_id": str(process_id), "flags": ["a", "b"]},
                idempotency_key="jsonb-job",
            )
            assert job is not None
            claimed = await claim(conn, worker_id)
            assert claimed is not None
            assert await complete(
                conn,
                claimed["id"],
                worker_id,
                {"ok": True, "count": 1},
            ) is True

            event = parse_event(
                {
                    "callback_id": "jsonb-callback",
                    "event_type": "request_completed",
                    "reference_type": "request",
                    "reference_id": "jsonb-request",
                    "payload": {"status": "completed", "tags": {"x": 1}},
                }
            )
            assert await _record_delivery(conn, event) is True

            row = await conn.fetchrow(
                """
                SELECT
                    jsonb_typeof(pv.source_payload) AS source_payload_type,
                    jsonb_typeof(p.subjects) AS subjects_type,
                    jsonb_typeof(p.parties) AS parties_type,
                    jsonb_typeof(p.header) AS header_type,
                    jsonb_typeof(ps.metadata) AS step_metadata_type,
                    jsonb_typeof(al.metadata) AS access_metadata_type,
                    pv.source_payload->>'event' AS source_event,
                    p.header->>'court' AS header_court
                FROM processes p
                JOIN process_versions pv ON pv.id = p.current_version_id
                JOIN process_steps ps ON ps.version_id = pv.id
                JOIN access_log al ON al.process_id = p.id
                WHERE p.id = $1
                """,
                process_id,
            )
            assert row is not None
            assert row["source_payload_type"] == "object"
            assert row["subjects_type"] == "array"
            assert row["parties_type"] == "array"
            assert row["header_type"] == "object"
            assert row["step_metadata_type"] == "object"
            assert row["access_metadata_type"] == "object"
            assert row["source_event"] == "response_created"
            assert row["header_court"] == "TJRS"

            queue_row = await conn.fetchrow(
                """
                SELECT jsonb_typeof(payload) AS payload_type,
                       jsonb_typeof(result) AS result_type,
                       payload->'flags'->>0 AS first_flag,
                       result->>'ok' AS result_ok
                FROM jobs
                WHERE id = $1
                """,
                claimed["id"],
            )
            assert queue_row["payload_type"] == "object"
            assert queue_row["result_type"] == "object"
            assert queue_row["first_flag"] == "a"
            assert queue_row["result_ok"] == "true"

            delivery = await conn.fetchrow(
                """
                SELECT jsonb_typeof(raw_payload) AS raw_type,
                       raw_payload->>'event_type' AS event_type
                FROM judit_deliveries
                WHERE callback_id = 'jsonb-callback'
                """
            )
            assert delivery["raw_type"] == "object"
            assert delivery["event_type"] == "request_completed"
    finally:
        await pool.close()
