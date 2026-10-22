from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.embedding_spaces import BGE_MODEL, COHERE_MODEL, EmbeddingSpace
from app.embedding_store import upsert_step_embeddings, vector_search_space
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)


async def _fixture(conn):
    process_id = uuid4()
    version_id = uuid4()
    step_id = uuid4()
    serial = f"{process_id.int % 100_000_000_000:011d}"
    code = f"{serial[:7]}-00.0000.0.00.{serial[7:]}"
    await conn.execute("INSERT INTO processes (id, code) VALUES ($1, $2)", process_id, code)
    await conn.execute(
        "INSERT INTO process_versions (id, process_id, source_request_id) VALUES ($1, $2, $3)",
        version_id,
        process_id,
        f"embedding-space-{version_id}",
    )
    await conn.execute(
        """
        INSERT INTO process_steps (id, version_id, process_id, step_number, text)
        VALUES ($1, $2, $3, 1, 'movimento sintético')
        """,
        step_id,
        version_id,
        process_id,
    )
    return version_id, step_id


@pytest.mark.asyncio
async def test_embedding_spaces_coexist_without_cross_provider_ranking() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    bge = EmbeddingSpace(provider="bge", model=BGE_MODEL)
    cohere = EmbeddingSpace(provider="cohere", model=COHERE_MODEL)
    try:
        async with pool.acquire() as conn:
            version_id, step_id = await _fixture(conn)
            await upsert_step_embeddings(
                conn,
                space=bge,
                items=[(step_id, [1.0] + [0.0] * 1023)],
            )
            await upsert_step_embeddings(
                conn,
                space=cohere,
                items=[(step_id, [0.0, 1.0] + [0.0] * 1022)],
            )

            row_count = await conn.fetchval(
                "SELECT count(*) FROM process_step_embeddings WHERE step_id = $1",
                step_id,
            )
            assert row_count == 2

            bge_results = await vector_search_space(
                conn,
                version_id=version_id,
                embedding=[1.0] + [0.0] * 1023,
                space=bge,
            )
            cohere_results = await vector_search_space(
                conn,
                version_id=version_id,
                embedding=[0.0, 1.0] + [0.0] * 1022,
                space=cohere,
            )
            assert bge_results[step_id] == pytest.approx(1.0)
            assert cohere_results[step_id] == pytest.approx(1.0)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_embedding_spaces_use_separate_partial_ann_indexes() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            indexes = await conn.fetch(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'process_step_embeddings'
                  AND indexname LIKE '%_hnsw'
                ORDER BY indexname
                """
            )
        by_name = {row["indexname"]: row["indexdef"] for row in indexes}
        assert "idx_process_step_embeddings_bge_m3_hnsw" in by_name
        assert "provider = 'bge'" in by_name["idx_process_step_embeddings_bge_m3_hnsw"]
        assert "idx_process_step_embeddings_cohere_v4_hnsw" in by_name
        assert "provider = 'cohere'" in by_name["idx_process_step_embeddings_cohere_v4_hnsw"]
    finally:
        await pool.close()
