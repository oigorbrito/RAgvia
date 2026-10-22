from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import asyncpg
import pytest

from app.attachment_context import load_attachment_context, resolve_generation_tenant
from app.attachments import (
    AttachmentChunkInput,
    replace_attachment_chunks,
    search_authorized_attachment_chunks,
    upsert_attachment_state,
)
from app.migrations import migrate
from app.provenance import (
    load_used_summary_sources,
    replace_summary_attachment_sources,
    selected_attachment_sources,
)
from app.rag import _load_context

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        TRUNCATE process_summary_attachment_sources, attachment_chunks, process_attachments,
                 process_summary_glossary_sources, process_summary_sources, process_summaries,
                 process_steps, tenant_processes, tenant_judit_requests, access_log,
                 process_versions, processes, tenants
        RESTART IDENTITY CASCADE
        """
    )


async def _fixture(conn: asyncpg.Connection):
    tenant_a = uuid4()
    tenant_b = uuid4()
    process_id = uuid4()
    version_id = uuid4()
    await conn.executemany(
        "INSERT INTO tenants (id, name) VALUES ($1, $2)",
        [(tenant_a, "Tenant A"), (tenant_b, "Tenant B")],
    )
    await conn.execute(
        """
        INSERT INTO processes (id, code, class_name, subjects, secrecy_level)
        VALUES ($1, $2, 'Execução', '[{"name":"penhora"}]'::jsonb, 0)
        """,
        process_id,
        "0000000-00.2026.8.21.1379",
    )
    await conn.execute(
        """
        INSERT INTO process_versions (id, process_id, source_request_id, finalized)
        VALUES ($1, $2, 'req-attachment-rag', TRUE)
        """,
        version_id,
        process_id,
    )
    await conn.execute(
        "UPDATE processes SET current_version_id=$2 WHERE id=$1",
        process_id,
        version_id,
    )
    await conn.execute(
        "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
        tenant_a,
        process_id,
    )
    await conn.execute(
        """
        INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id)
        VALUES ($1, $2, 'judit-rag-137')
        """,
        tenant_a,
        "0000000-00.2026.8.21.1379",
    )
    attachment_id = await upsert_attachment_state(
        conn,
        process_id=process_id,
        version_id=version_id,
        source_attachment_id="doc-penhora",
        status="pending",
        content_type="text/plain",
    )
    await replace_attachment_chunks(
        conn,
        attachment_id=attachment_id,
        process_id=process_id,
        version_id=version_id,
        chunks=[
            AttachmentChunkInput(
                text="Mandado de penhora cumprido e bem constrito.",
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=43,
            ),
            AttachmentChunkInput(
                text="Conteúdo sintético sem relação com a consulta.",
                page_start=2,
                page_end=2,
                char_start=0,
                char_end=45,
            ),
        ],
    )
    return tenant_a, tenant_b, process_id, version_id, attachment_id


@pytest.mark.asyncio
async def test_attachment_search_filters_authorization_before_ranking() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        tenant_a, tenant_b, process_id, version_id, _ = await _fixture(conn)

        rows = await search_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_a,
            process_id=process_id,
            version_id=version_id,
            query="penhora",
            limit=5,
        )
        assert len(rows) == 1
        assert rows[0]["source_attachment_id"] == "doc-penhora"
        assert "penhora" in str(rows[0]["text"]).lower()
        assert float(rows[0]["lexical_score"]) > 0

        assert await search_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_b,
            process_id=process_id,
            version_id=version_id,
            query="penhora",
            limit=5,
        ) == []
        assert await search_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_a,
            process_id=process_id,
            version_id=uuid4(),
            query="penhora",
            limit=5,
        ) == []

        await conn.execute("UPDATE processes SET secrecy_level=1 WHERE id=$1", process_id)
        assert await search_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_a,
            process_id=process_id,
            version_id=version_id,
            query="penhora",
            limit=5,
        ) == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_generation_tenant_and_context_are_bound_to_authorized_request() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await _reset(conn)
            tenant_a, _, process_id, version_id, _ = await _fixture(conn)

        assert await resolve_generation_tenant(
            pool,
            judit_request_id="judit-rag-137",
            process_id=process_id,
        ) == tenant_a
        assert await resolve_generation_tenant(
            pool,
            judit_request_id="unknown-request",
            process_id=process_id,
        ) is None

        attachments, sources, statuses = await load_attachment_context(
            pool,
            tenant_id=tenant_a,
            process_id=process_id,
            version_id=version_id,
            process_context={
                "class_name": "Execução",
                "subjects": [{"name": "penhora"}],
            },
            base_query="penhora",
        )
        assert len(attachments) == 1
        assert attachments[0]["source_attachment_id"] == "doc-penhora"
        assert "penhora" in attachments[0]["text"].lower()
        assert len(sources) == 1
        assert "text" not in sources[0]
        assert statuses == {"ready": 1}

        context = await _load_context(
            pool,
            process_id,
            version_id,
            tenant_id=tenant_a,
        )
        assert context["attachment_status"] == {"ready": 1}
        assert len(context["attachments"]) == 1
        assert "penhora" in context["attachments"][0]["text"].lower()
        assert len(context["_attachment_sources"]) == 1
        assert "text" not in context["_attachment_sources"][0]

        context_without_tenant = await _load_context(pool, process_id, version_id)
        assert "attachments" not in context_without_tenant
        assert context_without_tenant["_attachment_sources"] == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_secret_rag_context_never_loads_attachment_text() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await _reset(conn)
            tenant_a, _, process_id, version_id, _ = await _fixture(conn)
            await conn.execute("UPDATE processes SET secrecy_level=1 WHERE id=$1", process_id)

        context = await _load_context(
            pool,
            process_id,
            version_id,
            tenant_id=tenant_a,
        )
        assert context["secrecy_level"] == 1
        assert context["_attachment_sources"] == []
        assert "attachments" not in context
        assert "attachment_status" not in context
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_attachment_summary_provenance_is_text_free_and_scope_checked() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        tenant_a, _, process_id, version_id, attachment_id = await _fixture(conn)
        chunks = await search_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_a,
            process_id=process_id,
            version_id=version_id,
            query="penhora",
            limit=5,
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        sources = selected_attachment_sources(chunks)
        await replace_summary_attachment_sources(
            conn,
            summary_id=summary_id,
            process_id=process_id,
            version_id=version_id,
            sources=sources,
        )
        used = await load_used_summary_sources(conn, summary_id=summary_id)
        attachment_sources = [item for item in used if item["kind"] == "attachment"]
        assert len(attachment_sources) == 1
        source = attachment_sources[0]
        assert source["attachment_id"] == str(attachment_id)
        assert source["source_attachment_id"] == "doc-penhora"
        assert source["page_start"] == source["page_end"] == 1
        assert source["content_sha256"] == hashlib.sha256(
            b"Mandado de penhora cumprido e bem constrito."
        ).hexdigest()
        assert "text" not in source

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_attachment_sources (
                    summary_id, process_id, version_id, attachment_id,
                    attachment_chunk_id, source_attachment_id, content_sha256, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'wrong-source', $6, 9)
                """,
                summary_id,
                process_id,
                version_id,
                attachment_id,
                chunks[0]["chunk_id"],
                chunks[0]["content_sha256"],
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_attachment_fts_index_is_versioned_in_schema() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        definition = await conn.fetchval(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname='public' AND indexname='idx_attachment_chunks_text_fts'
            """
        )
        assert definition is not None
        assert "to_tsvector('portuguese'::regconfig, text)" in definition
    finally:
        await conn.close()
