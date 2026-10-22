from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app import judit_attachments
from app.db import create_pool
from app.judit_attachments import process_judit_attachments_task
from app.judit_client import JuditAttachmentDownload
from app.judit_tasks import finalize_judit_request_task
from app.migrations import migrate
from app.processes import stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _event(*, request_id: str, response_id: str, callback_id: str, code: str) -> dict:
    return {
        "callback_id": callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "instance": 1,
                "tribunal_acronym": "TJRS",
                "secrecy_level": 0,
                "classifications": [{"name": "Procedimento Comum"}],
                "parties": [],
                "subjects": [],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_date": "2026-09-18T00:00:00Z",
                        "content": "Movimento sintético",
                        "private": False,
                    }
                ],
                "attachments": [
                    {
                        "attachment_id": "att-1",
                        "attachment_name": "DECISAO.txt",
                        "attachment_date": "2026-09-18T00:00:00Z",
                        "status": "done",
                    }
                ],
            },
            "tags": {"cached_response": False},
        },
    }


@pytest.mark.asyncio
async def test_enabled_attachment_acquisition_runs_before_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")

    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE jobs, process_summary_attachment_sources, attachment_chunks,
                         process_attachments, process_summaries, process_steps,
                         tenant_processes, access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )

        code = "0000000-00.2026.8.21.0139"
        request_id = f"req-{uuid4()}"
        response_id = f"resp-{uuid4()}"
        callback_id = f"cb-{uuid4()}"
        event = _event(
            request_id=request_id,
            response_id=response_id,
            callback_id=callback_id,
            code=code,
        )
        async with pool.acquire() as conn:
            process_id, version_id = await stage_version(
                conn,
                code=code,
                source_request_id=response_id,
                cached_response=False,
                payload=event,
                judit_request_id=request_id,
                judit_response_id=response_id,
                judit_callback_id=callback_id,
            )

        finalized = await finalize_judit_request_task({"request_id": request_id})
        assert finalized["promoted"] is True
        assert finalized["attachment_processing_enqueued"] is True
        assert finalized["summary_enqueued"] is False

        async with pool.acquire() as conn:
            jobs = await conn.fetch(
                "SELECT task_name, status::text AS status FROM jobs ORDER BY created_at"
            )
            attachment = await conn.fetchrow(
                """
                SELECT status, provider_status
                FROM process_attachments
                WHERE process_id=$1 AND version_id=$2 AND source_attachment_id='att-1'
                """,
                process_id,
                version_id,
            )

        assert [row["task_name"] for row in jobs] == ["process_judit_attachments"]
        assert attachment["status"] == "pending"
        assert attachment["provider_status"] == "done"

        calls: list[tuple[str, str, str]] = []

        async def fake_download(code_arg: str, *, instance, attachment_id: str):
            calls.append((code_arg, str(instance), attachment_id))
            return JuditAttachmentDownload(
                content_type="text/plain",
                data=b"Decisao sintetica com fundamentacao suficiente para chunk local.",
            )

        monkeypatch.setattr(judit_attachments, "download_lawsuit_attachment", fake_download)

        result = await process_judit_attachments_task(
            {
                "process_id": str(process_id),
                "version_id": str(version_id),
                "judit_request_id": request_id,
            }
        )

        assert calls == [(code, "1", "att-1")]
        assert result["status"] == "completed"
        assert result["processed"] == 1
        assert result["pending"] == 0
        assert result["summary_enqueued"] is True

        async with pool.acquire() as conn:
            attachment = await conn.fetchrow(
                """
                SELECT status, content_type, byte_size, error_code
                FROM process_attachments
                WHERE process_id=$1 AND version_id=$2 AND source_attachment_id='att-1'
                """,
                process_id,
                version_id,
            )
            chunk_count = await conn.fetchval(
                "SELECT count(*) FROM attachment_chunks WHERE process_id=$1 AND version_id=$2",
                process_id,
                version_id,
            )
            summary_jobs = await conn.fetchval(
                "SELECT count(*) FROM jobs WHERE task_name='generate_process_summary'"
            )

        assert attachment["status"] == "ready"
        assert attachment["content_type"] == "text/plain"
        assert attachment["byte_size"] > 0
        assert attachment["error_code"] is None
        assert chunk_count == 1
        assert summary_jobs == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_explicit_download_rejection_is_attachment_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")

    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE jobs, process_summary_attachment_sources, attachment_chunks,
                         process_attachments, process_summaries, process_steps,
                         tenant_processes, access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )

        code = "0000000-00.2026.8.21.0140"
        request_id = f"req-{uuid4()}"
        response_id = f"resp-{uuid4()}"
        callback_id = f"cb-{uuid4()}"
        event = _event(
            request_id=request_id,
            response_id=response_id,
            callback_id=callback_id,
            code=code,
        )
        async with pool.acquire() as conn:
            process_id, version_id = await stage_version(
                conn,
                code=code,
                source_request_id=response_id,
                cached_response=False,
                payload=event,
                judit_request_id=request_id,
                judit_response_id=response_id,
                judit_callback_id=callback_id,
            )
        await finalize_judit_request_task({"request_id": request_id})

        async def rejected(*_args, **_kwargs):
            from app.judit_client import JuditRequestError

            raise JuditRequestError("Judit attachment download failed with HTTP 403", retry_safe=True)

        monkeypatch.setattr(judit_attachments, "download_lawsuit_attachment", rejected)

        result = await process_judit_attachments_task(
            {
                "process_id": str(process_id),
                "version_id": str(version_id),
                "judit_request_id": request_id,
            }
        )

        assert result["processed"] == 0
        assert result["unavailable"] == 1
        assert result["summary_enqueued"] is True

        async with pool.acquire() as conn:
            attachment = await conn.fetchrow(
                """
                SELECT status, error_code
                FROM process_attachments
                WHERE process_id=$1 AND version_id=$2 AND source_attachment_id='att-1'
                """,
                process_id,
                version_id,
            )
        assert attachment["status"] == "unavailable"
        assert attachment["error_code"] == "judit_download_rejected"
    finally:
        await pool.close()
