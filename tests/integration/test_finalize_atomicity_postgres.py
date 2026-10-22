from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

import app.judit_tasks as judit_tasks
from app.migrations import migrate
from app.processes import stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _judit_event(*, request_id: str, response_id: str, code: str) -> dict:
    return {
        "callback_id": f"callback-{uuid4()}",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "classifications": [{"name": "Classe teste"}],
                "courts": [{"name": "TJRS"}],
                "parties": [],
                "subjects": [],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_type": "MOVIMENTO",
                        "content": "movimento atomico",
                    }
                ],
            },
            "tags": {"cached_response": False},
        },
    }


@pytest.fixture(autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes
            RESTART IDENTITY CASCADE
            """
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_enqueue_failure_rolls_back_promotion_and_retry_finishes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    request_id = f"request-{uuid4()}"
    response_id = f"response-{uuid4()}"
    code = "0000000-00.0000.0.00.0801"

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=response_id,
            cached_response=False,
            payload=_judit_event(
                request_id=request_id,
                response_id=response_id,
                code=code,
            ),
            judit_request_id=request_id,
            judit_response_id=response_id,
        )
    finally:
        await conn.close()

    original_enqueue = judit_tasks.enqueue

    async def failing_enqueue(*args, **kwargs):
        raise RuntimeError("synthetic enqueue failure")

    monkeypatch.setattr(judit_tasks, "enqueue", failing_enqueue)
    with pytest.raises(RuntimeError, match="synthetic enqueue failure"):
        await judit_tasks.finalize_judit_request_task({"request_id": request_id})

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        state = await conn.fetchrow(
            """
            SELECT p.current_version_id, pv.finalized
            FROM processes p
            JOIN process_versions pv ON pv.process_id = p.id
            WHERE p.id = $1 AND pv.id = $2
            """,
            process_id,
            version_id,
        )
        steps = await conn.fetchval(
            "SELECT count(*) FROM process_steps WHERE version_id = $1",
            version_id,
        )
        jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"summary:{version_id}",
        )
    finally:
        await conn.close()

    assert state is not None
    assert state["current_version_id"] is None
    assert state["finalized"] is False
    assert steps == 0
    assert jobs == 0

    monkeypatch.setattr(judit_tasks, "enqueue", original_enqueue)
    result = await judit_tasks.finalize_judit_request_task({"request_id": request_id})
    assert result["status"] == "finalized"
    assert result["promoted"] is True
    assert result["summary_enqueued"] is True

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        state = await conn.fetchrow(
            """
            SELECT p.current_version_id, pv.finalized
            FROM processes p
            JOIN process_versions pv ON pv.process_id = p.id
            WHERE p.id = $1 AND pv.id = $2
            """,
            process_id,
            version_id,
        )
        steps = await conn.fetchval(
            "SELECT count(*) FROM process_steps WHERE version_id = $1",
            version_id,
        )
        jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"summary:{version_id}",
        )
    finally:
        await conn.close()

    assert state is not None
    assert state["current_version_id"] == version_id
    assert state["finalized"] is True
    assert steps == 1
    assert jobs == 1
