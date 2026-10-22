from __future__ import annotations

import os
from uuid import uuid4

import pytest

import app.embeddings as embeddings
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_step_embedding_skips_empty_movements_and_writes_valid_vector(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    process_id = uuid4()
    version_id = uuid4()
    empty_step_id = uuid4()
    content_step_id = uuid4()
    calls: list[list[str]] = []

    async def fake_embed_texts(texts):
        batch = list(texts)
        calls.append(batch)
        return [[0.0] * embeddings.VECTOR_DIMENSIONS for _ in batch]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                "INSERT INTO processes (id, code) VALUES ($1, $2)",
                process_id,
                "0000000-00.0000.0.00.0801",
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'embedding-test', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.executemany(
                """
                INSERT INTO process_steps (
                    id, version_id, process_id, step_number, title, text
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                [
                    (empty_step_id, version_id, process_id, 1, None, ""),
                    (content_step_id, version_id, process_id, 2, None, "conteúdo relevante"),
                ],
            )

        updated = await embeddings.ensure_step_embeddings(pool, version_id=version_id)
        assert updated == 1
        assert calls == [["conteúdo relevante"]]

        async with pool.acquire() as conn:
            empty_has_vector = await conn.fetchval(
                "SELECT embedding IS NOT NULL FROM process_steps WHERE id = $1",
                empty_step_id,
            )
            content_has_vector = await conn.fetchval(
                "SELECT embedding IS NOT NULL FROM process_steps WHERE id = $1",
                content_step_id,
            )
        assert empty_has_vector is False
        assert content_has_vector is True
    finally:
        await pool.close()
