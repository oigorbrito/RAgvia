from __future__ import annotations

import os
from uuid import uuid4

import pytest

import app.embeddings as embeddings
from app.db import create_pool
from app.embeddings import ensure_step_embeddings
from app.migrations import migrate
from app.retrieval import lexical_search, load_steps, vector_search

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_retrieval_excludes_mismatched_private_and_secret_steps() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    process_id = uuid4()
    version_id = uuid4()
    other_process_id = uuid4()
    other_version_id = uuid4()
    code = "0000000-00.2026.8.21.1121"
    other_code = "0000000-00.2026.8.21.1122"
    query_vector = [0.0] * 1536
    query_vector[0] = 1.0

    valid_id = uuid4()
    wrong_cnj_id = uuid4()
    wrong_instance_id = uuid4()
    private_id = uuid4()
    secret_id = uuid4()
    other_process_step_id = uuid4()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes
                RESTART IDENTITY CASCADE
                """
            )
            await conn.executemany(
                """
                INSERT INTO processes (id, code, secrecy_level, header)
                VALUES ($1, $2, 0, $3::jsonb)
                """,
                [
                    (process_id, code, '{"instance": 1}'),
                    (other_process_id, other_code, '{"instance": 1}'),
                ],
            )
            await conn.executemany(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, $3, TRUE)
                """,
                [
                    (version_id, process_id, "hard-filter-main"),
                    (other_version_id, other_process_id, "hard-filter-other"),
                ],
            )
            await conn.executemany(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                [(process_id, version_id), (other_process_id, other_version_id)],
            )

            rows = [
                (
                    valid_id,
                    version_id,
                    process_id,
                    1,
                    "Tutela pública válida",
                    '{"cnj":"%s","instance":1,"private":false,"secrecy_level":0}' % code,
                    query_vector,
                ),
                (
                    wrong_cnj_id,
                    version_id,
                    process_id,
                    2,
                    "Tutela com CNJ divergente",
                    '{"cnj":"%s","instance":1,"private":false,"secrecy_level":0}'
                    % other_code,
                    query_vector,
                ),
                (
                    wrong_instance_id,
                    version_id,
                    process_id,
                    3,
                    "Tutela com instância divergente",
                    '{"cnj":"%s","instance":2,"private":false,"secrecy_level":0}' % code,
                    query_vector,
                ),
                (
                    private_id,
                    version_id,
                    process_id,
                    4,
                    "Tutela privada não recuperável",
                    '{"cnj":"%s","instance":1,"private":true,"secrecy_level":0}' % code,
                    query_vector,
                ),
                (
                    secret_id,
                    version_id,
                    process_id,
                    5,
                    "Tutela sigilosa não recuperável",
                    '{"cnj":"%s","instance":1,"private":false,"secrecy_level":1}' % code,
                    query_vector,
                ),
                (
                    other_process_step_id,
                    other_version_id,
                    other_process_id,
                    1,
                    "Tutela de outro processo",
                    '{"cnj":"%s","instance":1,"private":false,"secrecy_level":0}'
                    % other_code,
                    query_vector,
                ),
            ]
            await conn.executemany(
                """
                INSERT INTO process_steps (
                    id, version_id, process_id, step_number, text, metadata, embedding
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::vector)
                """,
                rows,
            )

            steps = await load_steps(conn, version_id=version_id)
            lexical = await lexical_search(
                conn,
                version_id=version_id,
                query="tutela",
                limit=20,
            )
            vector = await vector_search(
                conn,
                version_id=version_id,
                embedding=query_vector,
                limit=20,
            )

        assert [step.id for step in steps] == [valid_id]
        assert set(lexical) == {valid_id}
        assert set(vector) == {valid_id}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_legacy_external_embedding_never_receives_private_or_secret_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.2026.8.21.1123"
    public_id = uuid4()
    private_id = uuid4()
    secret_id = uuid4()
    captured: list[str] = []

    monkeypatch.setattr(embeddings, "embedding_space_runtime_enabled", lambda: False)

    async def fake_embed_texts(texts):
        captured.extend(str(text) for text in texts)
        return [[0.25] * 1536 for _ in texts]

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
                """
                INSERT INTO processes (id, code, secrecy_level, header)
                VALUES ($1, $2, 0, '{"instance":1}'::jsonb)
                """,
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'embedding-hard-filter', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )
            await conn.executemany(
                """
                INSERT INTO process_steps (
                    id, version_id, process_id, step_number, text, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                [
                    (
                        public_id,
                        version_id,
                        process_id,
                        1,
                        "conteúdo público elegível",
                        '{"cnj":"%s","instance":1,"private":false,"secrecy_level":0}'
                        % code,
                    ),
                    (
                        private_id,
                        version_id,
                        process_id,
                        2,
                        "conteúdo privado proibido",
                        '{"cnj":"%s","instance":1,"private":true,"secrecy_level":0}'
                        % code,
                    ),
                    (
                        secret_id,
                        version_id,
                        process_id,
                        3,
                        "conteúdo sigiloso proibido",
                        '{"cnj":"%s","instance":1,"private":false,"secrecy_level":1}'
                        % code,
                    ),
                ],
            )

        updated = await ensure_step_embeddings(pool, version_id=version_id)

        async with pool.acquire() as conn:
            embedded = await conn.fetch(
                "SELECT id, embedding IS NOT NULL AS has_embedding FROM process_steps ORDER BY step_number"
            )

        assert updated == 1
        assert captured == ["conteúdo público elegível"]
        assert {row["id"]: row["has_embedding"] for row in embedded} == {
            public_id: True,
            private_id: False,
            secret_id: False,
        }
    finally:
        await pool.close()
