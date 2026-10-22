from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.judit import extract_promotable_fields
from app.migrations import migrate
from app.rag import _load_context, _persist_summary

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_public_tpu_glossary_reaches_rag_context_and_summary_provenance() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.2026.8.21.1383"

    promoted = extract_promotable_fields(
        {
            "code": code,
            "secrecy_level": 0,
            "classifications": [{"code": "7", "name": "Classe recebida"}],
            "subjects": [{"code": "5804", "name": "Assunto recebido"}],
            "steps": [],
        }
    )

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summary_glossary_sources, process_summary_sources,
                         process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                """
                INSERT INTO processes (
                    id, code, class_name, subjects, secrecy_level, header
                ) VALUES ($1, $2, $3, $4::jsonb, 0, $5::jsonb)
                """,
                process_id,
                code,
                promoted["class_name"],
                json.dumps(promoted["subjects"]),
                json.dumps(promoted["header"]),
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'tpu-public', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )

        context = await _load_context(pool, process_id, version_id)
        assert context["class_name"] == "Classe recebida"
        assert context["subjects"] == [{"code": "5804", "name": "Assunto recebido"}]
        assert "tpu_glossary" not in context["header"]
        glossary = context["tpu_glossary"]
        assert [(item["kind"], item["code"]) for item in glossary] == [
            ("class", "7"),
            ("subject", "5804"),
        ]
        assert {item["tpu_version"] for item in glossary} == {"2026-09-12"}
        assert len(context["_glossary_sources"]) == 2

        async with pool.acquire() as conn:
            persisted = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="# Resumo\n\nTexto sintético.",
                validation={"passed": True, "errors": []},
                generation_ms=1,
                glossary_sources=list(context["_glossary_sources"]),
            )
            rows = await conn.fetch(
                """
                SELECT kind, code, tpu_version, publisher, source, source_ref,
                       definition_sha256, source_order
                FROM process_summary_glossary_sources
                WHERE process_id=$1 AND version_id=$2
                ORDER BY source_order
                """,
                process_id,
                version_id,
            )
            stored_header = await conn.fetchval(
                "SELECT header FROM processes WHERE id=$1",
                process_id,
            )

        assert persisted is True
        assert "tpu_glossary" not in stored_header
        assert [(row["kind"], row["code"]) for row in rows] == [
            ("class", "7"),
            ("subject", "5804"),
        ]
        assert {row["tpu_version"] for row in rows} == {"2026-09-12"}
        assert all(len(row["definition_sha256"]) == 64 for row in rows)
        assert [row["source_order"] for row in rows] == [0, 1]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_unknown_codes_are_omitted_from_rag_glossary_without_invention() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    process_id = uuid4()
    version_id = uuid4()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summary_glossary_sources, process_summary_sources,
                         process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                """
                INSERT INTO processes (
                    id, code, class_name, subjects, secrecy_level, header
                ) VALUES ($1, '0000000-00.2026.8.21.1385', 'Classe recebida',
                          '[{"code":"888888","name":"Assunto recebido"}]'::jsonb,
                          0, '{"class_code":"999999"}'::jsonb)
                """,
                process_id,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'tpu-unknown', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )

        context = await _load_context(pool, process_id, version_id)
        assert "tpu_glossary" not in context
        assert context["_glossary_sources"] == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_secret_process_never_resolves_or_persists_tpu_glossary_provenance() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.2026.8.21.1384"

    promoted = extract_promotable_fields(
        {
            "code": code,
            "secrecy_level": 1,
            "classifications": [{"code": "7", "name": "Classe restrita"}],
            "subjects": [{"code": "5804", "name": "Assunto restrito"}],
            "steps": [],
        }
    )

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summary_glossary_sources, process_summary_sources,
                         process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                """
                INSERT INTO processes (
                    id, code, class_name, subjects, secrecy_level, header
                ) VALUES ($1, $2, $3, '[]'::jsonb, 1, $4::jsonb)
                """,
                process_id,
                code,
                promoted["class_name"],
                json.dumps(promoted["header"]),
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'tpu-secret', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )

        context = await _load_context(pool, process_id, version_id)
        assert context["subjects"] == []
        assert "tpu_glossary" not in context
        assert context["_glossary_sources"] == []

        async with pool.acquire() as conn:
            await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="# Resumo do processo\n\n## Sigilo\nRestrito.",
                validation={"passed": True, "errors": []},
                generation_ms=1,
                model="local-deterministic",
                prompt_version="secret-summary-v1",
                glossary_sources=list(context["_glossary_sources"]),
            )
            count = await conn.fetchval(
                "SELECT count(*) FROM process_summary_glossary_sources WHERE summary_id IN (SELECT id FROM process_summaries WHERE process_id=$1)",
                process_id,
            )

        assert count == 0
    finally:
        await pool.close()
