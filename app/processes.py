from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from app.attachments import sync_attachment_manifest
from app.json_utils import decode_json_list, decode_json_object
from app.process_semantics import SEMANTIC_SCHEMA_VERSION, semantic_fingerprint


async def get_authorized_process(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT p.*
        FROM processes p
        JOIN tenant_processes tp ON tp.process_id = p.id
        WHERE tp.tenant_id = $1 AND p.code = $2
        """,
        tenant_id,
        code,
    )


async def log_access(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_id: UUID | None,
    process_code: str,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO access_log (tenant_id, process_id, process_code, action, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        tenant_id,
        process_id,
        process_code,
        action,
        json.dumps(metadata or {}),
    )


async def grant_process_access(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_id: UUID,
) -> bool:
    """Bind a process to the tenant's authorized portfolio.

    Idempotent: returns True when a row was created, False when the binding
    already existed. Used at seed/startup time and on lawful webhook ingestion
    so a tenant that legitimately receives Judit callbacks for a CNJ can read
    the resulting process. Refusing to bind at ingestion would make every
    webhook-created process permanently invisible to its own tenant.
    """
    inserted = await conn.fetchrow(
        """
        INSERT INTO tenant_processes (tenant_id, process_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        RETURNING process_id
        """,
        tenant_id,
        process_id,
    )
    return inserted is not None


async def stage_version(
    conn: asyncpg.Connection,
    *,
    code: str,
    source_request_id: str | None,
    cached_response: bool,
    payload: dict[str, Any],
    judit_request_id: str | None = None,
    judit_response_id: str | None = None,
    judit_callback_id: str | None = None,
    tenant_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    async with conn.transaction():
        process_id = await conn.fetchval(
            """
            INSERT INTO processes (code)
            VALUES ($1)
            ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code
            RETURNING id
            """,
            code,
        )
        if tenant_id is not None:
            await grant_process_access(
                conn, tenant_id=tenant_id, process_id=process_id
            )
        version = await conn.fetchrow(
            """
            INSERT INTO process_versions (
                process_id,
                source_request_id,
                source_cached_response,
                source_payload,
                judit_request_id,
                judit_response_id,
                judit_callback_id
            )
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            ON CONFLICT (process_id, source_request_id)
            DO UPDATE SET
                source_cached_response = CASE
                    WHEN process_versions.finalized THEN process_versions.source_cached_response
                    ELSE EXCLUDED.source_cached_response
                END,
                source_payload = CASE
                    WHEN process_versions.finalized THEN process_versions.source_payload
                    ELSE EXCLUDED.source_payload
                END,
                judit_request_id = CASE
                    WHEN process_versions.finalized THEN process_versions.judit_request_id
                    ELSE COALESCE(EXCLUDED.judit_request_id, process_versions.judit_request_id)
                END,
                judit_response_id = CASE
                    WHEN process_versions.finalized THEN process_versions.judit_response_id
                    ELSE COALESCE(EXCLUDED.judit_response_id, process_versions.judit_response_id)
                END,
                judit_callback_id = CASE
                    WHEN process_versions.finalized THEN process_versions.judit_callback_id
                    ELSE COALESCE(EXCLUDED.judit_callback_id, process_versions.judit_callback_id)
                END
            RETURNING id, finalized
            """,
            process_id,
            source_request_id,
            cached_response,
            json.dumps(payload),
            judit_request_id,
            judit_response_id,
            judit_callback_id,
        )
        if version is None:
            raise RuntimeError("staged process version was not returned")
        if not bool(version["finalized"]):
            await conn.execute(
                "UPDATE processes SET updated_at = NOW() WHERE id = $1",
                process_id,
            )
    return process_id, version["id"]


async def preferred_judit_version(
    conn: asyncpg.Connection,
    *,
    request_id: str,
) -> asyncpg.Record | None:
    """Return fresh tribunal data when present, otherwise the latest cached response."""
    return await conn.fetchrow(
        """
        SELECT pv.id AS version_id,
               pv.process_id,
               pv.source_cached_response,
               pv.source_payload,
               pv.finalized,
               p.code
        FROM process_versions pv
        JOIN processes p ON p.id = pv.process_id
        WHERE pv.judit_request_id = $1
        ORDER BY pv.source_cached_response ASC, pv.created_at DESC
        LIMIT 1
        """,
        request_id,
    )


async def _current_semantic_fingerprint(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
) -> str | None:
    process = await conn.fetchrow(
        """
        SELECT court, class_name, subjects, parties, representatives, secrecy_level, header
        FROM processes
        WHERE id = $1 AND current_version_id = $2
        """,
        process_id,
        version_id,
    )
    if process is None:
        return None
    rows = await conn.fetch(
        """
        SELECT step_number, occurred_at, title, text, metadata
        FROM process_steps
        WHERE process_id = $1 AND version_id = $2
        ORDER BY step_number
        """,
        process_id,
        version_id,
    )
    attachment_rows = await conn.fetch(
        """
        SELECT source_attachment_id, source_name, source_date, provider_status
        FROM process_attachments
        WHERE process_id=$1 AND version_id=$2
        ORDER BY source_attachment_id
        """,
        process_id,
        version_id,
    )
    steps = [dict(row) for row in rows]
    attachments = [
        {
            "attachment_id": row["source_attachment_id"],
            "attachment_name": row["source_name"],
            "attachment_date": row["source_date"],
            "provider_status": row["provider_status"],
        }
        for row in attachment_rows
    ]
    return semantic_fingerprint(
        header=decode_json_object(process["header"], label="process header"),
        parties=decode_json_list(process["parties"], label="process parties"),
        representatives=decode_json_list(
            process["representatives"], label="process representatives"
        ),
        subjects=decode_json_list(process["subjects"], label="process subjects"),
        steps=steps,
        attachments=attachments,
        court=process["court"],
        class_name=process["class_name"],
        secrecy_level=int(process["secrecy_level"] or 0),
    )


async def finalize_version(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    header: dict[str, Any],
    parties: list[dict[str, Any]],
    subjects: list[Any],
    steps: list[dict[str, Any]],
    representatives: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    court: str | None = None,
    class_name: str | None = None,
    secrecy_level: int = 0,
) -> bool:
    """Finalize a version, avoiding promotion when normalized semantics are unchanged."""
    attachment_manifest = attachments or []
    candidate_fingerprint = semantic_fingerprint(
        header=header,
        parties=parties,
        representatives=representatives or [],
        subjects=subjects,
        steps=steps,
        attachments=attachment_manifest,
        court=court,
        class_name=class_name,
        secrecy_level=secrecy_level,
    )

    async with conn.transaction():
        process = await conn.fetchrow(
            """
            SELECT current_version_id
            FROM processes
            WHERE id = $1
            FOR UPDATE
            """,
            process_id,
        )
        if process is None:
            raise LookupError("process does not exist")

        candidate = await conn.fetchrow(
            """
            SELECT created_at, finalized
            FROM process_versions
            WHERE id = $1 AND process_id = $2
            FOR UPDATE
            """,
            version_id,
            process_id,
        )
        if candidate is None:
            raise LookupError("process version does not exist")

        current_version_id = process["current_version_id"]
        if bool(candidate["finalized"]):
            return current_version_id == version_id

        current_created_at = None
        if current_version_id is not None:
            current_created_at = await conn.fetchval(
                "SELECT created_at FROM process_versions WHERE id = $1",
                current_version_id,
            )
            current_fingerprint = await _current_semantic_fingerprint(
                conn,
                process_id=process_id,
                version_id=current_version_id,
            )
            if current_fingerprint == candidate_fingerprint:
                await sync_attachment_manifest(
                    conn,
                    process_id=process_id,
                    version_id=version_id,
                    attachments=attachment_manifest,
                )
                await conn.execute(
                    """
                    UPDATE process_versions
                    SET finalized = TRUE,
                        finalized_at = NOW(),
                        semantic_fingerprint = $3,
                        semantic_schema_version = $4,
                        equivalent_to_version_id = $5
                    WHERE id = $1 AND process_id = $2
                    """,
                    version_id,
                    process_id,
                    candidate_fingerprint,
                    SEMANTIC_SCHEMA_VERSION,
                    current_version_id,
                )
                return False

        await conn.execute("DELETE FROM process_steps WHERE version_id = $1", version_id)
        if steps:
            await conn.executemany(
                """
                INSERT INTO process_steps (
                    version_id, process_id, step_number, occurred_at, title, text, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                [
                    (
                        version_id,
                        process_id,
                        int(step["step_number"]),
                        step.get("occurred_at"),
                        step.get("title"),
                        str(step.get("text") or ""),
                        json.dumps(step.get("metadata") or {}),
                    )
                    for step in steps
                ],
            )

        await sync_attachment_manifest(
            conn,
            process_id=process_id,
            version_id=version_id,
            attachments=attachment_manifest,
        )

        await conn.execute(
            """
            UPDATE process_versions
            SET finalized = TRUE,
                finalized_at = NOW(),
                semantic_fingerprint = $3,
                semantic_schema_version = $4,
                equivalent_to_version_id = NULL
            WHERE id = $1 AND process_id = $2
            """,
            version_id,
            process_id,
            candidate_fingerprint,
            SEMANTIC_SCHEMA_VERSION,
        )

        promote = current_created_at is None or candidate["created_at"] >= current_created_at
        if promote:
            await conn.execute(
                """
                UPDATE processes
                SET court = $2,
                    class_name = $3,
                    subjects = $4::jsonb,
                    parties = $5::jsonb,
                    representatives = $6::jsonb,
                    secrecy_level = $7,
                    header = $8::jsonb,
                    current_version_id = $9,
                    updated_at = NOW()
                WHERE id = $1
                """,
                process_id,
                court,
                class_name,
                json.dumps(subjects),
                json.dumps(parties),
                json.dumps(representatives or []),
                secrecy_level,
                json.dumps(header),
                version_id,
            )

    return promote
