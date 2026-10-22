from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api_key_auth import RequestPrincipal, authorize_api_key_process
from app.auth import principal_from_request_unscoped
from app.judit import normalize_cnj
from app.judit_tracking import create_tracking, delete_tracking, get_tracking, list_trackings
from app.processes import get_authorized_process, log_access

router = APIRouter(prefix="/v1/trackings", tags=["trackings"])


class TrackingBatchRequest(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=100)
    recurrence_days: int = Field(default=1, ge=1, le=365)


def _tracking_payload(row: asyncpg.Record) -> dict:
    return {
        "tracking_id": str(row["id"]),
        "code": str(row["process_code"]),
        "status": str(row["status"]),
        "recurrence_days": int(row["recurrence_days"]),
        "last_event_at": row["last_event_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _authorized_principal_for_code(
    request: Request,
    *,
    principal: RequestPrincipal,
    code: str,
) -> RequestPrincipal:
    return await authorize_api_key_process(
        request,
        principal=principal,
        process_code=code,
    )


async def _audit_tracking_action(
    request: Request,
    *,
    tenant_id: UUID,
    code: str,
    action: str,
    metadata: dict | None = None,
) -> None:
    async with request.app.state.pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=code)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"] if process else None,
            process_code=code,
            action=action,
            metadata=metadata,
        )


async def _visible_trackings(
    conn: asyncpg.Connection,
    *,
    principal: RequestPrincipal,
) -> list[asyncpg.Record]:
    if not principal.uses_api_key:
        return list(await list_trackings(conn, tenant_id=principal.tenant_id))
    if principal.api_key_id is None:
        raise HTTPException(status_code=401, detail="invalid API key")

    return list(
        await conn.fetch(
            """
            SELECT jt.*
            FROM judit_trackings jt
            JOIN api_keys ak
              ON ak.id = $2
             AND ak.tenant_id = jt.tenant_id
            WHERE jt.tenant_id = $1
              AND (
                  EXISTS (
                      SELECT 1
                      FROM api_key_cnj_scopes s
                      WHERE s.api_key_id = ak.id
                        AND s.process_code = jt.process_code
                  )
                  OR (
                      ak.allow_portfolio
                      AND EXISTS (
                          SELECT 1
                          FROM tenant_processes tp
                          JOIN processes p ON p.id = tp.process_id
                          WHERE tp.tenant_id = jt.tenant_id
                            AND p.code = jt.process_code
                      )
                  )
              )
            ORDER BY jt.created_at, jt.id
            """,
            principal.tenant_id,
            principal.api_key_id,
        )
    )


@router.post("/{code}", status_code=202)
async def create_process_tracking(
    code: str,
    request: Request,
    recurrence_days: int = 1,
) -> dict:
    if recurrence_days < 1 or recurrence_days > 365:
        raise HTTPException(status_code=422, detail="recurrence_days must be between 1 and 365")
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

    principal = await principal_from_request_unscoped(request)
    await _authorized_principal_for_code(
        request,
        principal=principal,
        code=canonical_code,
    )
    result = await create_tracking(
        request.app.state.pool,
        tenant_id=principal.tenant_id,
        code=canonical_code,
        recurrence_days=recurrence_days,
    )
    await _audit_tracking_action(
        request,
        tenant_id=principal.tenant_id,
        code=canonical_code,
        action="create_process_tracking",
        metadata={"created": result.created, "recurrence_days": recurrence_days},
    )
    return {
        "tracking_id": str(result.tracking_id),
        "code": canonical_code,
        "status": result.status,
        "created": result.created,
    }


@router.post("", status_code=202)
async def create_tracking_batch(payload: TrackingBatchRequest, request: Request) -> dict:
    principal = await principal_from_request_unscoped(request)
    canonical_codes: list[str] = []
    seen: set[str] = set()
    for code in payload.codes:
        try:
            canonical = normalize_cnj(code)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid process code") from None
        if canonical not in seen:
            seen.add(canonical)
            canonical_codes.append(canonical)

    for code in canonical_codes:
        await _authorized_principal_for_code(
            request,
            principal=principal,
            code=code,
        )

    results = []
    for code in canonical_codes:
        result = await create_tracking(
            request.app.state.pool,
            tenant_id=principal.tenant_id,
            code=code,
            recurrence_days=payload.recurrence_days,
        )
        await _audit_tracking_action(
            request,
            tenant_id=principal.tenant_id,
            code=code,
            action="create_process_tracking",
            metadata={"created": result.created, "batch": True},
        )
        results.append(
            {
                "tracking_id": str(result.tracking_id),
                "code": code,
                "status": result.status,
                "created": result.created,
            }
        )
    return {"trackings": results}


@router.get("")
async def list_process_trackings(request: Request) -> dict:
    principal = await principal_from_request_unscoped(request)
    async with request.app.state.pool.acquire() as conn:
        rows = await _visible_trackings(conn, principal=principal)
        await log_access(
            conn,
            tenant_id=principal.tenant_id,
            process_id=None,
            process_code="*",
            action="list_process_trackings",
            metadata={"count": len(rows)},
        )
    return {"trackings": [_tracking_payload(row) for row in rows]}


@router.delete("/{tracking_id}", status_code=202)
async def remove_process_tracking(tracking_id: UUID, request: Request) -> dict:
    principal = await principal_from_request_unscoped(request)
    async with request.app.state.pool.acquire() as conn:
        row = await get_tracking(
            conn,
            tenant_id=principal.tenant_id,
            tracking_id=tracking_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="tracking not found")

    await _authorized_principal_for_code(
        request,
        principal=principal,
        code=str(row["process_code"]),
    )
    deleted = await delete_tracking(
        request.app.state.pool,
        tenant_id=principal.tenant_id,
        tracking_id=tracking_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="tracking not found")
    await _audit_tracking_action(
        request,
        tenant_id=principal.tenant_id,
        code=str(row["process_code"]),
        action="delete_process_tracking",
    )
    return {"tracking_id": str(tracking_id), "status": "deleting"}
