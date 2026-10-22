from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import asyncpg
import pytest

from app.attachments import (
    AttachmentChunkInput,
    attachment_status_counts,
    load_authorized_attachment_chunks,
    replace_attachment_chunks,
    upsert_attachment_state,
)
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


async def _process_fixture(
    conn: asyncpg.Connection,
    *,
    code: str,
    secrecy_level: int = 0,
) -> tuple[object, object]:
    process_id = uuid4()
    version_id = uuid4()
    await conn.execute(
        """
        INSERT INTO processes (id, code, secrecy_level)
        VALUES ($1, $2, $3)
        """,
        process_id,
        code,
        secrecy_level,
    )
    await conn.execute(
        """
        INSERT INTO process_versions (id, process_id, source_request_id, finalized)
        VALUES ($1, $2, $3, TRUE)
        """,
        version_id,
        process_id,
        f"req-{code}",
    )
    await conn.execute(
        "UPDATE processes SET current_version_id=$2 WHERE id=$1",
        process_id,
        version_id,
    )
    return process_id, version_id


@pytest.mark.asyncio
async def test_attachment_state_upsert_is_idempotent_and_tracks_failure_states() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_fixture(
            conn, code="0000000-00.2026.8.21.1371"
        )

        first_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-1",
            status="pending",
        )
        second_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-1",
            status="unavailable",
            error_code="source_unavailable",
        )
        third_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-2",
            status="corrupt",
            content_type="application/pdf",
            error_code="invalid_pdf",
        )

        assert first_id == second_id
        assert third_id != first_id
        rows = await conn.fetch(
            "SELECT source_attachment_id, status, error_code FROM process_attachments ORDER BY source_attachment_id"
        )
        assert [(row["source_attachment_id"], row["status"], row["error_code"]) for row in rows] == [
            ("att-1", "unavailable", "source_unavailable"),
            ("att-2", "corrupt", "invalid_pdf"),
        ]
        assert await attachment_status_counts(
            conn, process_id=process_id, version_id=version_id
        ) == {"unavailable": 1, "corrupt": 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_attachment_chunk_replacement_is_idempotent_and_preserves_source_positions() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_fixture(
            conn, code="0000000-00.2026.8.21.1372"
        )
        attachment_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-pdf",
            status="pending",
            content_type="application/pdf",
        )
        chunks = [
            AttachmentChunkInput(
                text="primeiro trecho sintético",
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=25,
            ),
            AttachmentChunkInput(
                text="segundo trecho sintético",
                page_start=2,
                page_end=2,
                char_start=26,
                char_end=50,
            ),
        ]

        assert await replace_attachment_chunks(
            conn,
            attachment_id=attachment_id,
            process_id=process_id,
            version_id=version_id,
            chunks=chunks,
        ) == 2
        assert await replace_attachment_chunks(
            conn,
            attachment_id=attachment_id,
            process_id=process_id,
            version_id=version_id,
            chunks=chunks,
        ) == 2

        rows = await conn.fetch(
            """
            SELECT chunk_index, text, page_start, page_end, char_start, char_end,
                   content_sha256
            FROM attachment_chunks
            WHERE attachment_id=$1
            ORDER BY chunk_index
            """,
            attachment_id,
        )
        assert len(rows) == 2
        assert [row["chunk_index"] for row in rows] == [0, 1]
        assert [(row["page_start"], row["page_end"]) for row in rows] == [(1, 1), (2, 2)]
        assert [(row["char_start"], row["char_end"]) for row in rows] == [(0, 25), (26, 50)]
        assert rows[0]["content_sha256"] == hashlib.sha256(
            chunks[0].text.encode("utf-8")
        ).hexdigest()
        assert await conn.fetchval(
            "SELECT status FROM process_attachments WHERE id=$1", attachment_id
        ) == "ready"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_attachment_chunk_scope_mismatch_is_rejected_by_database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_a, version_a = await _process_fixture(
            conn, code="0000000-00.2026.8.21.1373"
        )
        process_b, version_b = await _process_fixture(
            conn, code="0000000-00.2026.8.21.1374"
        )
        attachment_id = await upsert_attachment_state(
            conn,
            process_id=process_a,
            version_id=version_a,
            source_attachment_id="att-scope",
            status="pending",
        )

        with pytest.raises(asyncpg.RaiseError, match="attachment chunk scope"):
            await conn.execute(
                """
                INSERT INTO attachment_chunks (
                    attachment_id, process_id, version_id, chunk_index, text, content_sha256
                ) VALUES ($1, $2, $3, 0, 'fora de escopo', $4)
                """,
                attachment_id,
                process_b,
                version_b,
                hashlib.sha256(b"fora de escopo").hexdigest(),
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_authorized_attachment_retrieval_is_tenant_process_version_and_secrecy_scoped() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        tenant_a = uuid4()
        tenant_b = uuid4()
        await conn.executemany(
            "INSERT INTO tenants (id, name) VALUES ($1, $2)",
            [(tenant_a, "Tenant A"), (tenant_b, "Tenant B")],
        )
        public_process, public_version = await _process_fixture(
            conn, code="0000000-00.2026.8.21.1375"
        )
        secret_process, secret_version = await _process_fixture(
            conn, code="0000000-00.2026.8.21.1376", secrecy_level=1
        )
        await conn.executemany(
            "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
            [(tenant_a, public_process), (tenant_a, secret_process)],
        )

        public_attachment = await upsert_attachment_state(
            conn,
            process_id=public_process,
            version_id=public_version,
            source_attachment_id="att-ready",
            status="pending",
        )
        await replace_attachment_chunks(
            conn,
            attachment_id=public_attachment,
            process_id=public_process,
            version_id=public_version,
            chunks=[AttachmentChunkInput(text="contexto autorizado", page_start=1, page_end=1)],
        )
        await upsert_attachment_state(
            conn,
            process_id=public_process,
            version_id=public_version,
            source_attachment_id="att-corrupt",
            status="corrupt",
            error_code="invalid_pdf",
        )

        secret_attachment = await upsert_attachment_state(
            conn,
            process_id=secret_process,
            version_id=secret_version,
            source_attachment_id="att-secret",
            status="pending",
        )
        await replace_attachment_chunks(
            conn,
            attachment_id=secret_attachment,
            process_id=secret_process,
            version_id=secret_version,
            chunks=[AttachmentChunkInput(text="conteúdo secreto", page_start=1, page_end=1)],
        )

        tenant_a_rows = await load_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_a,
            process_id=public_process,
            version_id=public_version,
        )
        tenant_b_rows = await load_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_b,
            process_id=public_process,
            version_id=public_version,
        )
        secret_rows = await load_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_a,
            process_id=secret_process,
            version_id=secret_version,
        )

        assert len(tenant_a_rows) == 1
        assert tenant_a_rows[0]["source_attachment_id"] == "att-ready"
        assert tenant_a_rows[0]["text"] == "contexto autorizado"
        assert tenant_b_rows == []
        assert secret_rows == []
        assert await conn.fetchval("SELECT count(*) FROM process_steps") == 0
    finally:
        await conn.close()
