from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

PROGRESS_STATUSES = (
    "queued",
    "fetching",
    "indexing",
    "generating",
    "validating",
    "completed",
)
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "source_unavailable",
    "secrecy_blocked",
    "validation_failed",
}
ALL_STATUSES = set(PROGRESS_STATUSES) | TERMINAL_STATUSES
_PROGRESS_RANK = {status: index for index, status in enumerate(PROGRESS_STATUSES)}


class IdempotencyConflictError(ValueError):
    pass


class InvalidLifecycleTransition(ValueError):
    pass


@dataclass(frozen=True)
class PublicSummaryRequest:
    id: UUID
    tenant_id: UUID
    process_code: str
    idempotency_key: str
    request_fingerprint: str
    response_format: str
    status: str
    tenant_judit_request_id: UUID | None
    process_id: UUID | None
    version_id: UUID | None
    summary_id: UUID | None
    error_code: str | None


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_request(row: asyncpg.Record) -> PublicSummaryRequest:
    return PublicSummaryRequest(
        id=row["id"],
        tenant_id=row["tenant_id"],
        process_code=str(row["process_code"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        response_format=str(row["response_format"]),
        status=str(row["status"]),
        tenant_judit_request_id=row["tenant_judit_request_id"],
        process_id=row["process_id"],
        version_id=row["version_id"],
        summary_id=row["summary_id"],
        error_code=row["error_code"],
    )


async def create_or_get_summary_request(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_code: str,
    idempotency_key: str,
    fingerprint: str,
    response_format: str = "jsx",
) -> tuple[PublicSummaryRequest, bool]:
    """Create a durable public request or return the exact idempotent replay."""
    if not idempotency_key.strip():
        raise ValueError("idempotency key must not be empty")
    if response_format not in {"jsx", "json"}:
        raise ValueError("unsupported summary response format")

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO public_summary_requests (
                tenant_id, process_code, idempotency_key, request_fingerprint,
                response_format, status, completed_at
            )
            VALUES ($1, $2, $3, $4, $5, 'queued', NULL)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            tenant_id,
            process_code,
            idempotency_key,
            fingerprint,
            response_format,
        )
        if row is not None:
            return _as_request(row), True

        row = await conn.fetchrow(
            """
            SELECT *
            FROM public_summary_requests
            WHERE tenant_id = $1 AND idempotency_key = $2
            FOR UPDATE
            """,
            tenant_id,
            idempotency_key,
        )
        if row is None:
            raise RuntimeError("idempotent public summary request disappeared")
        if str(row["request_fingerprint"]) != fingerprint:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        return _as_request(row), False


async def get_summary_request_for_tenant(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    request_id: UUID,
) -> PublicSummaryRequest | None:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM public_summary_requests
        WHERE id = $1 AND tenant_id = $2
        """,
        request_id,
        tenant_id,
    )
    return _as_request(row) if row is not None else None


async def link_summary_request_to_acquisition(
    conn: asyncpg.Connection,
    *,
    request_id: UUID,
    tenant_id: UUID,
    process_code: str,
    tenant_judit_request_id: UUID,
) -> PublicSummaryRequest:
    """Attach a public job to a same-tenant/same-CNJ Judit acquisition."""
    row = await conn.fetchrow(
        """
        UPDATE public_summary_requests psr
        SET tenant_judit_request_id = tjr.id,
            updated_at = NOW()
        FROM tenant_judit_requests tjr
        WHERE psr.id = $1
          AND psr.tenant_id = $2
          AND psr.process_code = $3
          AND tjr.id = $4
          AND tjr.tenant_id = psr.tenant_id
          AND tjr.process_code = psr.process_code
          AND (
              psr.tenant_judit_request_id IS NULL
              OR psr.tenant_judit_request_id = tjr.id
          )
        RETURNING psr.*
        """,
        request_id,
        tenant_id,
        process_code,
        tenant_judit_request_id,
    )
    if row is None:
        raise LookupError("public summary request cannot be linked to acquisition")
    return _as_request(row)


def _transition_allowed(current: str, target: str) -> bool:
    if current == target:
        return True
    if current in TERMINAL_STATUSES:
        return False
    if target in TERMINAL_STATUSES:
        return True
    return _PROGRESS_RANK[target] >= _PROGRESS_RANK[current]


async def transition_summary_request(
    conn: asyncpg.Connection,
    *,
    request_id: UUID,
    status: str,
    process_id: UUID | None = None,
    version_id: UUID | None = None,
    summary_id: UUID | None = None,
    source_updated_at=None,
    flags: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> PublicSummaryRequest:
    """Advance one public request without allowing stale work to regress it."""
    if status not in ALL_STATUSES:
        raise InvalidLifecycleTransition(f"unknown public summary status: {status}")

    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM public_summary_requests WHERE id = $1 FOR UPDATE",
            request_id,
        )
        if row is None:
            raise LookupError("public summary request does not exist")

        current = str(row["status"])
        if not _transition_allowed(current, status):
            # A retry/reclaimed stale job must never downgrade a terminal or later
            # public state. Returning the current row makes that behavior idempotent.
            return _as_request(row)

        completed_at_sql = "NOW()" if status in TERMINAL_STATUSES else "NULL"
        updated = await conn.fetchrow(
            f"""
            UPDATE public_summary_requests
            SET status = $2::public_summary_status,
                process_id = COALESCE($3, process_id),
                version_id = COALESCE($4, version_id),
                summary_id = COALESCE($5, summary_id),
                source_updated_at = COALESCE($6, source_updated_at),
                flags = CASE WHEN $7::jsonb IS NULL THEN flags ELSE flags || $7::jsonb END,
                error_code = $8,
                completed_at = {completed_at_sql},
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            request_id,
            status,
            process_id,
            version_id,
            summary_id,
            source_updated_at,
            json.dumps(flags) if flags is not None else None,
            error_code,
        )
        if updated is None:
            raise RuntimeError("public summary request update returned no row")
        return _as_request(updated)


async def _transition_many(
    conn: asyncpg.Connection,
    request_ids: list[UUID],
    *,
    status: str,
    process_id: UUID | None = None,
    version_id: UUID | None = None,
    summary_id: UUID | None = None,
    source_updated_at=None,
    flags: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> int:
    transitioned = 0
    for request_id in request_ids:
        await transition_summary_request(
            conn,
            request_id=request_id,
            status=status,
            process_id=process_id,
            version_id=version_id,
            summary_id=summary_id,
            source_updated_at=source_updated_at,
            flags=flags,
            error_code=error_code,
        )
        transitioned += 1
    return transitioned


async def transition_requests_for_acquisition(
    conn: asyncpg.Connection,
    *,
    tenant_judit_request_id: UUID,
    status: str,
    process_id: UUID | None = None,
    version_id: UUID | None = None,
    summary_id: UUID | None = None,
    source_updated_at=None,
    flags: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> int:
    rows = await conn.fetch(
        "SELECT id FROM public_summary_requests WHERE tenant_judit_request_id = $1",
        tenant_judit_request_id,
    )
    return await _transition_many(
        conn,
        [row["id"] for row in rows],
        status=status,
        process_id=process_id,
        version_id=version_id,
        summary_id=summary_id,
        source_updated_at=source_updated_at,
        flags=flags,
        error_code=error_code,
    )


async def transition_requests_for_judit_request(
    conn: asyncpg.Connection,
    *,
    judit_request_id: str,
    status: str,
    process_id: UUID | None = None,
    version_id: UUID | None = None,
    summary_id: UUID | None = None,
    source_updated_at=None,
    flags: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> int:
    rows = await conn.fetch(
        """
        SELECT psr.id
        FROM public_summary_requests psr
        JOIN tenant_judit_requests tjr
          ON tjr.id = psr.tenant_judit_request_id
        WHERE tjr.judit_request_id = $1
        """,
        judit_request_id,
    )
    return await _transition_many(
        conn,
        [row["id"] for row in rows],
        status=status,
        process_id=process_id,
        version_id=version_id,
        summary_id=summary_id,
        source_updated_at=source_updated_at,
        flags=flags,
        error_code=error_code,
    )


async def transition_requests_for_version(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    status: str,
    process_id: UUID | None = None,
    summary_id: UUID | None = None,
    source_updated_at=None,
    flags: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> int:
    rows = await conn.fetch(
        "SELECT id FROM public_summary_requests WHERE version_id = $1",
        version_id,
    )
    return await _transition_many(
        conn,
        [row["id"] for row in rows],
        status=status,
        process_id=process_id,
        version_id=version_id,
        summary_id=summary_id,
        source_updated_at=source_updated_at,
        flags=flags,
        error_code=error_code,
    )


async def transition_job_public_requests(
    conn: asyncpg.Connection,
    *,
    task_name: str,
    payload: dict[str, Any],
    status: str,
    error_code: str | None = None,
) -> int:
    """Resolve queue topology to public jobs without exposing queue IDs publicly."""
    try:
        if task_name == "request_judit_process":
            tenant_request_id = UUID(str(payload["tenant_request_id"]))
            return await transition_requests_for_acquisition(
                conn,
                tenant_judit_request_id=tenant_request_id,
                status=status,
                error_code=error_code,
            )
        if task_name == "finalize_judit_request":
            return await transition_requests_for_judit_request(
                conn,
                judit_request_id=str(payload["request_id"]),
                status=status,
                error_code=error_code,
            )
        if task_name == "generate_process_summary":
            judit_request_id = payload.get("judit_request_id")
            if judit_request_id:
                return await transition_requests_for_judit_request(
                    conn,
                    judit_request_id=str(judit_request_id),
                    status=status,
                    error_code=error_code,
                )
            version_id = UUID(str(payload["version_id"]))
            return await transition_requests_for_version(
                conn,
                version_id=version_id,
                status=status,
                error_code=error_code,
            )
    except (KeyError, TypeError, ValueError):
        return 0
    return 0
