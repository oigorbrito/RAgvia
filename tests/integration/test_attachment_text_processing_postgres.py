from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import asyncpg
import pytest

from app.attachment_processing import AttachmentProcessingLimits, process_text_attachment_bytes
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        TRUNCATE attachment_chunks, process_attachments, process_summary_glossary_sources,
                 process_summary_sources, process_summaries, process_steps,
                 tenant_processes, access_log, process_versions, processes, tenants
        RESTART IDENTITY CASCADE
        """
    )


async def _fixture(conn: asyncpg.Connection):
    process_id = uuid4()
    version_id = uuid4()
    await conn.execute(
        "INSERT INTO processes (id, code, secrecy_level) VALUES ($1, $2, 0)",
        process_id,
        "0000000-00.2026.8.21.1377",
    )
    await conn.execute(
        """
        INSERT INTO process_versions (id, process_id, source_request_id, finalized)
        VALUES ($1, $2, 'req-attachment-text', TRUE)
        """,
        version_id,
        process_id,
    )
    await conn.execute(
        "UPDATE processes SET current_version_id=$2 WHERE id=$1",
        process_id,
        version_id,
    )
    return process_id, version_id


@pytest.mark.asyncio
async def test_text_processing_persists_ready_chunks_and_is_idempotent() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _fixture(conn)
        data = (("trecho sintético de anexo " * 30) + "fim").encode("utf-8")
        limits = AttachmentProcessingLimits(max_bytes=20_000, chunk_chars=256)

        first = await process_text_attachment_bytes(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-text-1",
            content_type="text/plain; charset=utf-8",
            data=data,
            limits=limits,
        )
        second = await process_text_attachment_bytes(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-text-1",
            content_type="text/plain; charset=utf-8",
            data=data,
            limits=limits,
        )

        assert first["attachment_id"] == second["attachment_id"]
        assert first["status"] == second["status"] == "ready"
        assert first["chunk_count"] == second["chunk_count"]
        row = await conn.fetchrow(
            """
            SELECT status, content_type, byte_size, content_sha256, error_code
            FROM process_attachments
            WHERE id=$1
            """,
            first["attachment_id"],
        )
        assert row["status"] == "ready"
        assert row["content_type"] == "text/plain"
        assert row["byte_size"] == len(data)
        assert row["content_sha256"] == hashlib.sha256(data).hexdigest()
        assert row["error_code"] is None
        assert await conn.fetchval(
            "SELECT count(*) FROM attachment_chunks WHERE attachment_id=$1",
            first["attachment_id"],
        ) == first["chunk_count"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_text_processing_failure_is_local_and_makes_old_chunks_ineligible() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _fixture(conn)
        limits = AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256)

        ready = await process_text_attachment_bytes(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-text-2",
            content_type="text/plain",
            data=b"conteudo sintetico valido",
            limits=limits,
        )
        failed = await process_text_attachment_bytes(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-text-2",
            content_type="text/plain",
            data=b"\xff\xfe",
            limits=limits,
        )

        assert ready["attachment_id"] == failed["attachment_id"]
        assert failed == {
            "attachment_id": ready["attachment_id"],
            "status": "corrupt",
            "error_code": "invalid_utf8",
            "chunk_count": 0,
        }
        assert await conn.fetchval(
            "SELECT status FROM process_attachments WHERE id=$1", ready["attachment_id"]
        ) == "corrupt"
        assert await conn.fetchval("SELECT count(*) FROM process_steps") == 0
        assert await conn.fetchval(
            """
            SELECT count(*)
            FROM attachment_chunks ac
            JOIN process_attachments pa ON pa.id=ac.attachment_id
            WHERE ac.attachment_id=$1 AND pa.status='ready'
            """,
            ready["attachment_id"],
        ) == 0
    finally:
        await conn.close()
