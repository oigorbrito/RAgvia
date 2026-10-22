from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import asyncpg

from app.attachment_processing import process_attachment_bytes
from app.attachments import upsert_attachment_state
from app.db import create_pool
from app.json_utils import decode_json_object
from app.judit_client import (
    JuditRequestError,
    download_lawsuit_attachment,
    judit_attachments_enabled,
)
from app.queue import enqueue
from app.tasks import PermanentTaskError, task


async def _enqueue_summary(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    code: str,
    judit_request_id: str,
) -> bool:
    job = await enqueue(
        conn,
        task_name="generate_process_summary",
        payload={
            "process_id": str(process_id),
            "version_id": str(version_id),
            "code": code,
            "judit_request_id": judit_request_id,
        },
        idempotency_key=f"summary:{version_id}",
    )
    return job is not None


@task("process_judit_attachments")
async def process_judit_attachments_task(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        process_id = UUID(str(payload["process_id"]))
        version_id = UUID(str(payload["version_id"]))
        judit_request_id = str(payload["judit_request_id"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("invalid Judit attachment processing payload") from exc
    if not judit_request_id:
        raise PermanentTaskError("invalid Judit attachment processing payload")
    if not judit_attachments_enabled():
        raise PermanentTaskError("Judit attachment processing is disabled")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                """
                SELECT p.code, p.secrecy_level, p.current_version_id, p.header
                FROM processes p
                JOIN process_versions pv
                  ON pv.id=$2
                 AND pv.process_id=p.id
                 AND pv.finalized=TRUE
                WHERE p.id=$1
                """,
                process_id,
                version_id,
            )
            if process is None:
                raise PermanentTaskError("process/version not found for attachment processing")
            if process["current_version_id"] != version_id:
                return {"status": "stale_version", "summary_enqueued": False}
            if int(process["secrecy_level"] or 0) > 0:
                raise PermanentTaskError("secret process cannot acquire attachments")

            header = decode_json_object(process["header"], label="process header")
            instance = header.get("instance")
            if instance is None or str(instance).strip() == "":
                raise PermanentTaskError("process instance is required for attachment download")
            code = str(process["code"])

            rows = await conn.fetch(
                """
                SELECT source_attachment_id, provider_status
                FROM process_attachments
                WHERE process_id=$1
                  AND version_id=$2
                  AND status='pending'
                ORDER BY source_attachment_id
                """,
                process_id,
                version_id,
            )

            processed = 0
            unavailable = 0
            for row in rows:
                source_attachment_id = str(row["source_attachment_id"])
                provider_status = str(row["provider_status"] or "").strip().lower()
                if provider_status and provider_status != "done":
                    raise RuntimeError("Judit attachment source is not ready")

                try:
                    download = await download_lawsuit_attachment(
                        code,
                        instance=instance,
                        attachment_id=source_attachment_id,
                    )
                except JuditRequestError as exc:
                    if not exc.retry_safe:
                        raise
                    await upsert_attachment_state(
                        conn,
                        process_id=process_id,
                        version_id=version_id,
                        source_attachment_id=source_attachment_id,
                        status="unavailable",
                        error_code="judit_download_rejected",
                    )
                    unavailable += 1
                    continue

                await process_attachment_bytes(
                    conn,
                    process_id=process_id,
                    version_id=version_id,
                    source_attachment_id=source_attachment_id,
                    content_type=download.content_type,
                    data=download.data,
                )
                processed += 1

            pending = int(
                await conn.fetchval(
                    """
                    SELECT count(*)
                    FROM process_attachments
                    WHERE process_id=$1 AND version_id=$2 AND status='pending'
                    """,
                    process_id,
                    version_id,
                )
                or 0
            )
            if pending:
                raise RuntimeError("Judit attachments remain pending")

            summary_enqueued = await _enqueue_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                code=code,
                judit_request_id=judit_request_id,
            )
            return {
                "status": "completed",
                "processed": processed,
                "unavailable": unavailable,
                "pending": 0,
                "summary_enqueued": summary_enqueued,
            }
    finally:
        await pool.close()
