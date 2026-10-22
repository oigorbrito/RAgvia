from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from app.api_v1 import _load_sources
from app.db import create_pool
from app.migrations import migrate
from app.provenance import load_used_summary_sources
from app.rag import _persist_summary

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)


async def _fixture(conn, *, secrecy_level: int = 0):
    process_id = uuid4()
    version_id = uuid4()
    serial = f"{process_id.int % 100_000_000_000:011d}"
    code = f"{serial[:7]}-00.0000.0.00.{serial[7:]}"
    await conn.execute(
        "INSERT INTO processes (id, code, secrecy_level) VALUES ($1, $2, $3)",
        process_id,
        code,
        secrecy_level,
    )
    await conn.execute(
        """
        INSERT INTO process_versions (
            id, process_id, source_request_id, finalized, finalized_at
        ) VALUES ($1, $2, $3, TRUE, NOW())
        """,
        version_id,
        process_id,
        f"source-{version_id}",
    )
    await conn.execute(
        "UPDATE processes SET current_version_id = $2 WHERE id = $1",
        process_id,
        version_id,
    )

    step_ids = [uuid4(), uuid4(), uuid4()]
    for number, step_id in enumerate(step_ids, start=1):
        await conn.execute(
            """
            INSERT INTO process_steps (
                id, version_id, process_id, step_number, title, text, metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            step_id,
            version_id,
            process_id,
            number,
            f"Movimento {number}",
            f"payload sensível {number}",
            json.dumps({"source_step_number": number * 10}),
        )
    return process_id, version_id, step_ids


@pytest.mark.asyncio
async def test_summary_provenance_tracks_only_selected_movements_and_replaces_on_regeneration() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, step_ids = await _fixture(conn)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="invalid",
                validation={"passed": False, "errors": ["retry"]},
                generation_ms=1,
                selected_sources=[
                    {
                        "step_id": step_ids[0],
                        "step_number": 1,
                        "occurred_at": None,
                        "source_order": 0,
                    },
                    {
                        "step_id": step_ids[2],
                        "step_number": 3,
                        "occurred_at": None,
                        "source_order": 1,
                    },
                ],
            )
            summary_id = await conn.fetchval(
                "SELECT id FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )
            first = await load_used_summary_sources(conn, summary_id=summary_id)
            assert [source["step_number"] for source in first] == [1, 3]
            assert all("text" not in source for source in first)

            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="valid",
                validation={"passed": True, "errors": []},
                generation_ms=2,
                selected_sources=[
                    {
                        "step_id": step_ids[1],
                        "step_number": 2,
                        "occurred_at": None,
                        "source_order": 0,
                    }
                ],
            )
            second = await load_used_summary_sources(conn, summary_id=summary_id)
            assert [source["step_number"] for source in second] == [2]
            assert second[0]["source_step_number"] == 20
            assert second[0]["used_for_summary"] is True

            public_sources = await _load_sources(
                conn,
                process_id=process_id,
                version_id=version_id,
                summary_id=summary_id,
            )
            movement_sources = [source for source in public_sources if source["kind"] == "movement"]
            assert [source["step_number"] for source in movement_sources] == [2]
            assert movement_sources[0]["step_id"] == str(step_ids[1])
            assert all("payload" not in str(source) for source in public_sources)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_secret_process_never_exposes_movement_level_provenance() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, step_ids = await _fixture(conn, secrecy_level=1)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="sigiloso",
                validation={"passed": True, "errors": []},
                generation_ms=1,
                model="local-deterministic",
                prompt_version="secret-summary-v1",
                selected_sources=[],
            )
            summary_id = await conn.fetchval(
                "SELECT id FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )
            sources = await _load_sources(
                conn,
                process_id=process_id,
                version_id=version_id,
                summary_id=summary_id,
            )

            assert [source["kind"] for source in sources] == ["judit_lawsuit"]
            assert str(step_ids[0]) not in str(sources)
            assert "payload sensível" not in str(sources)
    finally:
        await pool.close()
