from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.judit_tasks import finalize_judit_request_task
from app.migrations import migrate
from app.processes import finalize_version, stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _fields(label: str) -> dict:
    return {
        "header": {"label": label},
        "parties": [],
        "subjects": [],
        "steps": [
            {
                "step_number": 1,
                "occurred_at": None,
                "title": "MOVIMENTO",
                "text": f"movimento {label}",
                "metadata": {},
            }
        ],
        "court": "TJRS",
        "class_name": f"Classe {label}",
        "secrecy_level": 0,
    }


def _judit_event(*, request_id: str, response_id: str, code: str, label: str) -> dict:
    return {
        "callback_id": f"callback-{label}-{uuid4()}",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "classifications": [{"name": f"Classe {label}"}],
                "courts": [{"name": "TJRS"}],
                "parties": [],
                "subjects": [],
                "steps": [
                    {
                        "step_id": f"step-{label}",
                        "step_type": "MOVIMENTO",
                        "content": f"movimento {label}",
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
async def test_older_version_finalized_late_cannot_replace_newer_current() -> None:
    assert TEST_DATABASE_URL is not None
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        code = "0000000-00.0000.0.00.0701"
        process_id, older_id = await stage_version(
            conn,
            code=code,
            source_request_id="older-response",
            cached_response=False,
            payload={"source": "older"},
        )
        _, newer_id = await stage_version(
            conn,
            code=code,
            source_request_id="newer-response",
            cached_response=False,
            payload={"source": "newer"},
        )

        now = datetime.now(UTC)
        await conn.execute(
            "UPDATE process_versions SET created_at = $2 WHERE id = $1",
            older_id,
            now - timedelta(minutes=10),
        )
        await conn.execute(
            "UPDATE process_versions SET created_at = $2 WHERE id = $1",
            newer_id,
            now,
        )

        assert await finalize_version(
            conn,
            process_id=process_id,
            version_id=newer_id,
            **_fields("nova"),
        ) is True
        assert await finalize_version(
            conn,
            process_id=process_id,
            version_id=older_id,
            **_fields("antiga"),
        ) is False

        process = await conn.fetchrow(
            "SELECT current_version_id, class_name, header FROM processes WHERE id = $1",
            process_id,
        )
        assert process is not None
        assert process["current_version_id"] == newer_id
        assert process["class_name"] == "Classe nova"
        assert "nova" in str(process["header"])

        finalized = await conn.fetch(
            "SELECT id, finalized FROM process_versions WHERE process_id = $1",
            process_id,
        )
        assert {row["id"]: row["finalized"] for row in finalized} == {
            older_id: True,
            newer_id: True,
        }
        step_versions = await conn.fetch(
            "SELECT version_id, text FROM process_steps WHERE process_id = $1",
            process_id,
        )
        assert {row["version_id"] for row in step_versions} == {older_id, newer_id}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_stale_judit_finalizer_does_not_enqueue_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        code = "0000000-00.0000.0.00.0702"
        old_request = f"request-old-{uuid4()}"
        new_request = f"request-new-{uuid4()}"
        old_response = f"response-old-{uuid4()}"
        new_response = f"response-new-{uuid4()}"

        process_id, older_id = await stage_version(
            conn,
            code=code,
            source_request_id=old_response,
            cached_response=False,
            payload=_judit_event(
                request_id=old_request,
                response_id=old_response,
                code=code,
                label="antiga",
            ),
            judit_request_id=old_request,
            judit_response_id=old_response,
        )
        _, newer_id = await stage_version(
            conn,
            code=code,
            source_request_id=new_response,
            cached_response=False,
            payload=_judit_event(
                request_id=new_request,
                response_id=new_response,
                code=code,
                label="nova",
            ),
            judit_request_id=new_request,
            judit_response_id=new_response,
        )

        now = datetime.now(UTC)
        await conn.execute(
            "UPDATE process_versions SET created_at = $2 WHERE id = $1",
            older_id,
            now - timedelta(minutes=10),
        )
        await conn.execute(
            "UPDATE process_versions SET created_at = $2 WHERE id = $1",
            newer_id,
            now,
        )
        assert await finalize_version(
            conn,
            process_id=process_id,
            version_id=newer_id,
            **_fields("nova"),
        ) is True
    finally:
        await conn.close()

    result = await finalize_judit_request_task({"request_id": old_request})
    assert result["status"] == "finalized_stale"
    assert result["promoted"] is False
    assert result["summary_enqueued"] is False

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        assert await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE id = $1",
            process_id,
        ) == newer_id
        assert await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"summary:{older_id}",
        ) == 0
    finally:
        await conn.close()
