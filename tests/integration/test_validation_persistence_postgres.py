from __future__ import annotations

import os
from uuid import uuid4

import pytest

import app.rag as rag
from app.db import create_pool
from app.json_utils import decode_json_object
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_second_invalid_generation_is_persisted_as_failed_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.0000.0.00.0501"
    calls: list[list[str] | None] = []

    async def always_invalid(client, context, validation_errors=None):
        calls.append(validation_errors)
        return f"Processo {code}. Recomendo que a parte tome providências."

    monkeypatch.setattr(rag, "_generate", always_invalid)

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
                INSERT INTO processes (
                    id, code, class_name, parties, subjects, header
                ) VALUES ($1, $2, 'Classe de teste', '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)
                """,
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'validation-failure', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id = $2 WHERE id = $1",
                process_id,
                version_id,
            )
            await conn.execute(
                """
                INSERT INTO process_steps (version_id, process_id, step_number, title, text)
                VALUES ($1, $2, 1, 'MOVIMENTO', 'Movimento processual de teste')
                """,
                version_id,
                process_id,
            )

        result = await rag.generate_summary(pool, process_id, version_id)

        assert len(calls) == 2
        assert calls[0] is None
        assert calls[1]
        assert any("prognostic" in error for error in calls[1])
        assert result["validation"]["passed"] is False
        assert any("prognostic" in error for error in result["validation"]["errors"])

        async with pool.acquire() as conn:
            stored = await conn.fetchrow(
                """
                SELECT markdown, validation, model
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process_id,
                version_id,
            )

        assert stored is not None
        validation = decode_json_object(stored["validation"], label="summary validation")
        assert validation["passed"] is False
        assert any("prognostic" in error for error in validation["errors"])
        assert "Recomendo que" in stored["markdown"]
        assert stored["model"] == "claude-sonnet-5"
    finally:
        await pool.close()
