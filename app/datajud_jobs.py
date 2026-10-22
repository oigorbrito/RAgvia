from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth import principal_from_request, principal_from_request_unscoped
from app.datajud_client import datajud_alias_from_cnj, datajud_activation_status
from app.datajud_security import (
    DataJudProcessKnownSecret,
    DataJudProcessNotAuthorized,
    DataJudProcessNotPublic,
    classify_datajud_lookup,
    require_public_datajud_process,
)
from app.judit import normalize_cnj
from app.json_utils import decode_json_object
from app.queue import enqueue, get_idempotent_job

DATAJUD_TASK = "lookup_datajud_metadata"
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class DataJudJobSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str


class DataJudJobStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    job_id: UUID


def metadata_response(code: str, metadata: dict[str, Any]) -> dict[str, Any]:
    subjects = metadata.get("subjects")
    if not isinstance(subjects, list):
        subjects = []
    clean_subjects = [
        {key: value for key, value in subject.items() if key in {"code", "name"} and isinstance(value, str)}
        for subject in subjects
        if isinstance(subject, dict)
    ]
    return {
        "code": code,
        "source": "datajud",
        "court": datajud_alias_from_cnj(code).upper(),
        "class_name": metadata.get("class_name") if isinstance(metadata.get("class_name"), str) else None,
        "adjudicating_body": metadata.get("adjudicating_body") if isinstance(metadata.get("adjudicating_body"), str) else None,
        "county": metadata.get("county") if isinstance(metadata.get("county"), str) else None,
        "subjects": clean_subjects,
        "parties": [],
    }


def _activation_or_raise() -> None:
    enabled, reason = datajud_activation_status()
    if not enabled:
        raise HTTPException(status_code=403, detail=reason or "DataJud lookup is not authorized")


async def _tenant_id(request: Request, code: str) -> UUID:
    principal = await principal_from_request(request, process_code=code)
    return principal.tenant_id


async def _authorize_lookup(conn, *, tenant_id: UUID, code: str) -> tuple[str, int | None]:
    try:
        return await classify_datajud_lookup(conn, tenant_id=tenant_id, code=code)
    except DataJudProcessKnownSecret as exc:
        raise HTTPException(status_code=403, detail="DataJud lookup is unavailable for a known restricted process") from exc


async def _authorize_job_read(conn, *, tenant_id: UUID, code: str, payload: dict[str, Any]) -> None:
    if (
        payload.get("tenant_id") != str(tenant_id)
        or payload.get("code") != code
        or payload.get("operation") != "metadata_lookup"
        or payload.get("purpose") != "public_metadata_lookup"
    ):
        raise HTTPException(status_code=404, detail="DataJud job not found")
    classification = payload.get("classification_at_request")
    if classification not in {"verified_public", "unknown"}:
        raise HTTPException(status_code=404, detail="DataJud job not found")
    if classification == "verified_public":
        try:
            await require_public_datajud_process(conn, tenant_id=tenant_id, code=code)
        except DataJudProcessNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="DataJud process not found") from exc
        except DataJudProcessNotPublic as exc:
            raise HTTPException(status_code=403, detail="DataJud lookup requires a verified public process") from exc
    else:
        await _authorize_lookup(conn, tenant_id=tenant_id, code=code)


async def reject_synchronous_datajud_lookup(request: Request, raw_code: str) -> None:
    try:
        code = normalize_cnj(raw_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid CNJ") from exc
    await _tenant_id(request, code)
    _activation_or_raise()
    raise HTTPException(
        status_code=405,
        detail="Use the asynchronous DataJud job endpoint",
        headers={"Allow": "POST"},
    )


async def submit_datajud_lookup(request: Request, raw_code: str) -> JSONResponse:
    try:
        code = normalize_cnj(raw_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid CNJ") from exc
    tenant_id = await _tenant_id(request, code)
    _activation_or_raise()

    keys = request.headers.getlist("idempotency-key")
    if len(keys) != 1 or not _IDEMPOTENCY_RE.fullmatch(keys[0]):
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key header is required")
    idempotency_key = f"datajud:{tenant_id}:{keys[0]}"
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Queue unavailable")

    payload = {
        "tenant_id": str(tenant_id), "code": code,
        "operation": "metadata_lookup",
        "purpose": "public_metadata_lookup",
    }
    async with pool.acquire() as conn:
        async with conn.transaction():
            classification, _level = await _authorize_lookup(conn, tenant_id=tenant_id, code=code)
            payload["classification_at_request"] = classification
            row = await get_idempotent_job(conn, idempotency_key)
            if row is not None:
                stored = decode_json_object(row["payload"], label="DataJud job payload")
                if row["task_name"] != DATAJUD_TASK:
                    raise HTTPException(status_code=503, detail="Queue unavailable")
                if stored.get("tenant_id") != str(tenant_id) or stored.get("code") != code:
                    raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different request")
                created = False
            else:
                stored = payload
                created = False

            if row is not None and stored.get("purpose") != "public_metadata_lookup":
                raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different operation")
            if row is None:
                row = await enqueue(
                    conn,
                    task_name=DATAJUD_TASK,
                    payload=payload,
                    idempotency_key=idempotency_key,
                )
                created = row is not None
                if row is None:
                    row = await get_idempotent_job(conn, idempotency_key)
                    stored = (
                        decode_json_object(row["payload"], label="DataJud job payload")
                        if row is not None
                        else {}
                    )
            if row is None or row["task_name"] != DATAJUD_TASK:
                raise HTTPException(status_code=503, detail="Queue unavailable")
            if stored.get("tenant_id") != str(tenant_id) or stored.get("code") != code:
                raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different request")
    return JSONResponse(
        status_code=202,
        content={"job_id": str(row["id"]), "code": code, "status": str(row["status"]), "created": created},
    )


async def list_datajud_history(request: Request) -> JSONResponse:
    principal = await principal_from_request_unscoped(request)
    tenant_id = principal.tenant_id
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Queue unavailable")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, status::text AS status, payload, result, created_at
               FROM jobs WHERE task_name=$1 AND payload->>'tenant_id'=$2
               ORDER BY created_at DESC LIMIT 50""",
            DATAJUD_TASK, str(tenant_id),
        )
        items = []
        for row in rows:
            payload = decode_json_object(row["payload"], label="DataJud job payload")
            try:
                code = normalize_cnj(str(payload.get("code", "")))
                await _authorize_job_read(
                    conn, tenant_id=tenant_id, code=code, payload=payload,
                )
            except (ValueError, HTTPException):
                continue
            result = decode_json_object(row["result"], label="DataJud job result") if row["result"] else {}
            raw_metadata = result.get("metadata") if result.get("status") == "ok" else None
            metadata = metadata_response(code, raw_metadata) if isinstance(raw_metadata, dict) else None
            items.append({
                "job_id": str(row["id"]), "code": code,
                "status": str(row["status"]), "created_at": row["created_at"].isoformat(),
                "outcome": result.get("status"), "metadata": metadata,
                "class_name": metadata.get("class_name") if metadata else None,
                "court": metadata.get("court") if metadata else None,
            })
    return JSONResponse(content={"items": items})


async def get_datajud_lookup(request: Request, raw_code: str, job_id: UUID) -> JSONResponse:
    try:
        code = normalize_cnj(raw_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid CNJ") from exc
    tenant_id = await _tenant_id(request, code)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Queue unavailable")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT status::text AS status, payload, result FROM jobs
               WHERE id=$1 AND task_name=$2 AND payload->>'tenant_id'=$3 AND payload->>'code'=$4""",
            job_id, DATAJUD_TASK, str(tenant_id), code,
        )
        if row is not None:
            payload = decode_json_object(row["payload"], label="DataJud job payload")
            await _authorize_job_read(
                conn, tenant_id=tenant_id, code=code, payload=payload,
            )
    if row is None:
        raise HTTPException(status_code=404, detail="DataJud job not found")
    status = str(row["status"])
    if status in {"pending", "processing"}:
        return JSONResponse(status_code=202, content={"job_id": str(job_id), "code": code, "status": status})
    if status == "dead":
        raise HTTPException(status_code=502, detail="DataJud lookup failed")
    if status != "completed":
        raise HTTPException(status_code=502, detail="DataJud lookup is unavailable")
    result = decode_json_object(row["result"], label="DataJud job result")
    outcome = result.get("status")
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Process not found in DataJud")
    if outcome != "ok":
        raise HTTPException(status_code=502, detail="DataJud lookup is unavailable")
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=502, detail="DataJud lookup is unavailable")
    return JSONResponse(status_code=200, content=metadata_response(code, metadata))
