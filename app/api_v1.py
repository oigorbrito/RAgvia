from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api_key_auth import (
    RequestPrincipal,
    apply_rate_limit_headers,
    authorize_api_key_process,
    log_principal_access,
)
from app.attachment_signals import attachment_status_flags
from app.auth import principal_from_request, principal_from_request_unscoped, tenant_from_request
from app.claim_evidence import claim_evidence_is_publishable, load_summary_claim_evidence
from app.datajud_client import datajud_activation_status
from app.datajud_jobs import (
    DataJudJobStatusRequest,
    DataJudJobSubmission,
    get_datajud_lookup,
    list_datajud_history,
    reject_synchronous_datajud_lookup,
    submit_datajud_lookup,
)
from app.judit import normalize_cnj
from app.json_utils import decode_json_object, loads_strict_json
from app.process_query_safety import classify_cnj_status, create_authorization
from app.process_requests import request_process
from app.processes import get_authorized_process, log_access
from app.provenance import load_used_summary_sources
from app.public_lifecycle import (
    IdempotencyConflictError,
    create_or_get_summary_request,
    request_fingerprint,
)
from app.summary_policy import is_restricted_local_summary

router = APIRouter()


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    return decode_json_object(value, label="public API JSON object")


def _response(payload: dict[str, Any], *, principal: RequestPrincipal, status_code: int = 200):
    response = JSONResponse(status_code=status_code, content=jsonable_encoder(payload))
    apply_rate_limit_headers(response, principal)
    return response


def _summary_format(value: Any) -> str:
    rendered = str(value or "jsx").strip().lower()
    if rendered not in {"jsx", "json"}:
        raise HTTPException(status_code=400, detail="format must be 'jsx' or 'json'")
    return rendered


def _summary_source(value: Any) -> str:
    rendered = str(value or "judit").strip().lower()
    if rendered != "judit":
        raise HTTPException(status_code=400, detail="source must be 'judit'")
    return rendered


def _summary_representation(
    *,
    markdown: str | None,
    structured_output: Any,
    response_format: str,
    hide_claim_evidence: bool = False,
    claim_evidence: list[dict[str, Any]] | None = None,
) -> Any:
    if response_format == "jsx":
        return markdown
    if structured_output is None:
        return None
    rendered = _json_object(structured_output)
    summary = rendered.get("summary")
    if not isinstance(summary, dict) or "claims" not in summary:
        return rendered
    if hide_claim_evidence:
        public_summary = dict(summary)
        public_summary.pop("claims", None)
        return {**rendered, "summary": public_summary}

    evidence_by_claim = {
        str(item.get("claim_id")): item
        for item in (claim_evidence or [])
        if isinstance(item, dict) and item.get("claim_id") is not None
    }
    public_claims: list[Any] = []
    for raw_claim in summary.get("claims", []):
        if not isinstance(raw_claim, dict):
            public_claims.append(raw_claim)
            continue
        claim = dict(raw_claim)
        evidence = evidence_by_claim.get(str(claim.get("claim_id")))
        if evidence is not None:
            claim["verification"] = {
                "status": evidence.get("verification_status"),
                "reason": evidence.get("verification_reason"),
                "sources": evidence.get("sources", []),
            }
        public_claims.append(claim)
    public_summary = dict(summary)
    public_summary["claims"] = public_claims
    return {**rendered, "summary": public_summary}


def _public_claim_evidence(
    claim_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for item in claim_evidence:
        if not isinstance(item, dict):
            continue
        public_item = dict(item)
        public_sources: list[dict[str, Any]] = []
        for source in item.get("sources", []):
            if not isinstance(source, dict):
                continue
            public_source = dict(source)
            public_source.pop("evidence_excerpt", None)
            public_sources.append(public_source)
        public_item["sources"] = public_sources
        rendered.append(public_item)
    return rendered


def _summary_usage(row: asyncpg.Record) -> dict[str, Any] | None:
    if row["model"] is None:
        return None
    result: dict[str, Any] = {
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "generation_ms": int(row["generation_ms"] or 0),
    }
    provider_usage = _json_object(row["provider_usage"])
    result.update(provider_usage)
    if row["cache_hit"] is not None:
        result["cache_hit"] = bool(row["cache_hit"])
    if row["cost_usd"] is not None:
        result["cost_usd"] = float(row["cost_usd"])
    return result


async def _load_attachment_flags(
    conn: asyncpg.Connection,
    *,
    process_id: UUID | None,
    version_id: UUID | None,
) -> dict[str, Any]:
    if process_id is None or version_id is None:
        return {}
    row = await conn.fetchrow(
        """
        SELECT pending_count, ready_count, unavailable_count, corrupt_count, unreadable_count
        FROM process_attachment_status_counts
        WHERE process_id=$1 AND version_id=$2
        """,
        process_id,
        version_id,
    )
    if row is None:
        return {}
    return attachment_status_flags(
        {
            "pending": row["pending_count"],
            "ready": row["ready_count"],
            "unavailable": row["unavailable_count"],
            "corrupt": row["corrupt_count"],
            "unreadable": row["unreadable_count"],
        }
    )


async def _load_sources(
    conn: asyncpg.Connection,
    *,
    process_id: UUID | None,
    version_id: UUID | None,
    summary_id: UUID | None = None,
) -> list[dict[str, Any]]:
    if process_id is None or version_id is None:
        return []

    version = await conn.fetchrow(
        """
        SELECT pv.id, pv.source_cached_response, pv.finalized_at, p.secrecy_level
        FROM process_versions pv
        JOIN processes p ON p.id = pv.process_id
        WHERE pv.id = $1 AND pv.process_id = $2 AND pv.finalized = TRUE
        """,
        version_id,
        process_id,
    )
    if version is None:
        return []

    root_source: dict[str, Any] = {
        "kind": "judit_lawsuit",
        "source_version": str(version["id"]),
        "cached": bool(version["source_cached_response"]),
        "finalized_at": version["finalized_at"],
    }
    if summary_id is not None:
        root_source["used_for_summary"] = True
    sources: list[dict[str, Any]] = [root_source]

    # Restricted processes never expose movement-level provenance through the
    # public sources endpoint, even if older rows exist from a prior policy.
    if int(version["secrecy_level"] or 0) > 0:
        return sources

    if summary_id is not None:
        used_sources = await load_used_summary_sources(conn, summary_id=summary_id)
        if used_sources:
            sources.extend(used_sources)
            return sources

    # Compatibility fallback for summaries created before provenance persistence
    # and for process/source reads that do not yet have a published summary.
    steps = await conn.fetch(
        """
        SELECT id,
               step_number,
               occurred_at,
               title,
               CASE
                   WHEN jsonb_typeof(metadata->'source_step_number') = 'number'
                   THEN (metadata->>'source_step_number')::bigint
                   ELSE NULL
               END AS source_step_number
        FROM process_steps
        WHERE process_id = $1 AND version_id = $2
        ORDER BY step_number
        """,
        process_id,
        version_id,
    )
    sources.extend(
        {
            "kind": "movement",
            "step_id": str(row["id"]),
            "step_number": int(row["step_number"]),
            "source_step_number": (
                int(row["source_step_number"])
                if row["source_step_number"] is not None
                else None
            ),
            "occurred_at": row["occurred_at"],
            "title": row["title"],
        }
        for row in steps
    )
    return sources


async def _job_payload(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    job_id: UUID,
) -> tuple[asyncpg.Record, dict[str, Any]] | None:
    row = await conn.fetchrow(
        """
        SELECT psr.*,
               ps.markdown,
               ps.validation,
               ps.model,
               ps.prompt_version,
               ps.generation_ms,
               ps.usage AS provider_usage,
               ps.cache_hit,
               ps.cost_usd,
               ps.structured_output,
               p.secrecy_level AS process_secrecy_level,
               p.code AS summary_process_code,
               p.class_name AS summary_process_class_name,
               p.court AS summary_process_court,
               p.header AS summary_process_header,
               p.parties AS summary_process_parties,
               p.representatives AS summary_process_representatives
        FROM public_summary_requests psr
        LEFT JOIN process_summaries ps ON ps.id = psr.summary_id
        LEFT JOIN processes p ON p.id = psr.process_id
        WHERE psr.id = $1 AND psr.tenant_id = $2
        """,
        job_id,
        tenant_id,
    )
    if row is None:
        return None
    claim_evidence = (
        await load_summary_claim_evidence(conn, summary_id=row["summary_id"])
        if row["summary_id"] is not None
        else []
    )
    validation = (
        _json_object(row["validation"])
        if row["validation"] is not None
        else None
    )
    flags = _json_object(row["flags"])
    flags.update(
        await _load_attachment_flags(
            conn,
            process_id=row["process_id"],
            version_id=row["version_id"],
        )
    )
    structured_output = (
        _json_object(row["structured_output"])
        if row["structured_output"] is not None
        else None
    )
    is_secret = int(row["process_secrecy_level"] or 0) > 0
    publishable = bool(
        validation
        and validation.get("passed") is True
        and (
            (
                is_secret
                and is_restricted_local_summary(
                    row,
                    process={
                        "code": row["summary_process_code"],
                        "class_name": row["summary_process_class_name"],
                        "header": row["summary_process_header"],
                    },
                )
            )
            or (
                not is_secret
                and claim_evidence_is_publishable(
                    structured_output,
                    claim_evidence,
                    process={
                        "code": row["summary_process_code"],
                        "class_name": row["summary_process_class_name"],
                        "court": row["summary_process_court"],
                        "header": row["summary_process_header"],
                        "parties": row["summary_process_parties"],
                        "representatives": row["summary_process_representatives"],
                    },
                )
            )
        )
    )
    sources = await _load_sources(
        conn,
        process_id=row["process_id"],
        version_id=row["version_id"],
        summary_id=row["summary_id"] if publishable else None,
    )
    public_claim_evidence = (
        _public_claim_evidence(claim_evidence)
        if publishable and not is_secret
        else []
    )
    payload = {
        "job_id": str(row["id"]),
        "poll_url": f"/v1/resumos/{row['id']}",
        "status": str(row["status"]),
        "cnj": str(row["process_code"]),
        "source_updated_at": row["source_updated_at"],
        "sources": sources,
        "usage": _summary_usage(row),
        "flags": flags,
        "validation": validation,
        "claim_evidence": public_claim_evidence,
        "format": str(row["response_format"]),
        "iaSummary": (
            _summary_representation(
                markdown=row["markdown"],
                structured_output=row["structured_output"],
                response_format=str(row["response_format"]),
                hide_claim_evidence=is_secret,
                claim_evidence=public_claim_evidence,
            )
            if publishable
            else None
        ),
        "error_code": row["error_code"],
    }
    return row, payload


async def _latest_summary_payload(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
    response_format: str = "jsx",
) -> dict[str, Any] | None:
    process = await get_authorized_process(conn, tenant_id=tenant_id, code=code)
    if process is None or process["current_version_id"] is None:
        return None
    summary = await conn.fetchrow(
        """
        SELECT id, markdown, structured_output, validation, model, prompt_version,
               generation_ms, usage AS provider_usage, cache_hit, cost_usd, created_at
        FROM process_summaries
        WHERE process_id = $1
          AND version_id = $2
          AND COALESCE((validation->>'passed')::boolean, false) = true
        """,
        process["id"],
        process["current_version_id"],
    )
    if summary is None:
        return None
    validation = _json_object(summary["validation"])
    claim_evidence = await load_summary_claim_evidence(
        conn, summary_id=summary["id"]
    )
    structured_output = (
        _json_object(summary["structured_output"])
        if summary["structured_output"] is not None
        else None
    )
    is_secret = int(process["secrecy_level"] or 0) > 0
    if is_secret:
        if not is_restricted_local_summary(summary, process=process):
            return None
    elif not claim_evidence_is_publishable(
        structured_output,
        claim_evidence,
        process=process,
    ):
        return None
    sources = await _load_sources(
        conn,
        process_id=process["id"],
        version_id=process["current_version_id"],
        summary_id=summary["id"],
    )
    flags = {"secrecy": is_secret}
    flags.update(
        await _load_attachment_flags(
            conn,
            process_id=process["id"],
            version_id=process["current_version_id"],
        )
    )
    return {
        "cnj": code,
        "source_updated_at": process["updated_at"],
        "sources": sources,
        "usage": _summary_usage(summary),
        "flags": flags,
        "validation": validation,
        "claim_evidence": (
            []
            if int(process["secrecy_level"] or 0) > 0
            else _public_claim_evidence(claim_evidence)
        ),
        "format": response_format,
        "iaSummary": _summary_representation(
            markdown=summary["markdown"],
            structured_output=summary["structured_output"],
            response_format=response_format,
            hide_claim_evidence=is_secret,
            claim_evidence=_public_claim_evidence(claim_evidence),
        ),
    }


@router.post("/v1/resumos", status_code=202)
async def create_summary_job(request: Request):
    idempotency_values = request.headers.getlist("Idempotency-Key")
    if not idempotency_values:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if len(idempotency_values) != 1:
        raise HTTPException(status_code=400, detail="Idempotency-Key must appear exactly once")
    idempotency_key = idempotency_values[0].strip()
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if "," in idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key must appear exactly once")
    if len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")

    try:
        body = loads_strict_json(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON payload") from None
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="request body must contain strict JSON",
        ) from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    raw_code = body.get("cnj")
    if not isinstance(raw_code, str):
        raise HTTPException(status_code=400, detail="cnj is required")
    try:
        code = normalize_cnj(raw_code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    response_format = _summary_format(body.get("format"))
    source = _summary_source(body.get("source"))

    principal = await principal_from_request(request, process_code=code)
    normalized_body = dict(body)
    normalized_body["cnj"] = code
    normalized_body["format"] = response_format
    normalized_body["source"] = source
    try:
        fingerprint = request_fingerprint(normalized_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="request body must contain strict JSON") from None
    pool: asyncpg.Pool = request.app.state.pool

    try:
        async with pool.acquire() as conn:
            public_request, created = await create_or_get_summary_request(
                conn,
                tenant_id=principal.tenant_id,
                process_code=code,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response_format=response_format,
            )
    except IdempotencyConflictError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        ) from None

    if created or public_request.tenant_judit_request_id is None:
        auth_token = (
            request.headers.get("X-Acquisition-Authorization")
            or request.headers.get("X-Judit-Authorization-Token")
            or request.headers.get("X-Confirmation-Token")
            or body.get("authorization_token")
            or body.get("confirmation_token")
        )
        if auth_token:
            auth_token = str(auth_token).strip()
        await request_process(
            pool,
            tenant_id=principal.tenant_id,
            code=code,
            public_summary_request_id=public_request.id,
            authorization_token=auth_token,
        )

    async with pool.acquire() as conn:
        loaded = await _job_payload(
            conn,
            tenant_id=principal.tenant_id,
            job_id=public_request.id,
        )
        if loaded is None:
            raise RuntimeError("public summary request disappeared after creation")
        _, payload = loaded
        await log_principal_access(
            conn,
            principal=principal,
            process_id=public_request.process_id,
            process_code=code,
            action="v1_create_summary",
            metadata={"created": created},
        )
    return _response(payload, principal=principal, status_code=202)


@router.get("/v1/resumos/{job_id}")
async def get_summary_job(job_id: str, request: Request):
    try:
        request_id = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="summary job not found") from None

    principal = await principal_from_request_unscoped(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        loaded = await _job_payload(
            conn,
            tenant_id=principal.tenant_id,
            job_id=request_id,
        )
    if loaded is None:
        raise HTTPException(status_code=404, detail="summary job not found")
    row, payload = loaded

    principal = await authorize_api_key_process(
        request,
        principal=principal,
        process_code=str(row["process_code"]),
    )
    async with pool.acquire() as conn:
        await log_principal_access(
            conn,
            principal=principal,
            process_id=row["process_id"],
            process_code=str(row["process_code"]),
            action="v1_read_summary_job",
        )
    return _response(payload, principal=principal)


@router.get("/v1/processos/{code}/resumo")
async def get_latest_summary(code: str, request: Request):
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    response_format = _summary_format(request.query_params.get("format"))
    tenant_id = tenant_from_request(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        payload = await _latest_summary_payload(
            conn,
            tenant_id=tenant_id,
            code=canonical_code,
            response_format=response_format,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="summary not found")
        state = getattr(request, "state", None)
        principal = getattr(state, "principal", None)
        if not isinstance(principal, RequestPrincipal):
            await log_access(
                conn,
                tenant_id=tenant_id,
                process_id=None,
                process_code=canonical_code,
                action="v1_read_summary",
            )
    return payload


@router.get("/v1/processos/{code}/fontes")
async def get_summary_sources(code: str, request: Request):
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    tenant_id = tenant_from_request(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        if process is None:
            raise HTTPException(status_code=404, detail="process not found")
        summary_id = None
        summary_structured_output = None
        if process["current_version_id"] is not None:
            summary_row = await conn.fetchrow(
                """
                SELECT id, structured_output, model, prompt_version
                FROM process_summaries
                WHERE process_id = $1
                  AND version_id = $2
                  AND COALESCE((validation->>'passed')::boolean, false) = true
                """,
                process["id"],
                process["current_version_id"],
            )
            if summary_row is not None:
                is_secret = int(process["secrecy_level"] or 0) > 0
                if not is_secret or is_restricted_local_summary(
                    summary_row, process=process
                ):
                    summary_id = summary_row["id"]
                    summary_structured_output = (
                        _json_object(summary_row["structured_output"])
                        if summary_row["structured_output"] is not None
                        else None
                    )
        state = getattr(request, "state", None)
        principal = getattr(state, "principal", None)
        if not isinstance(principal, RequestPrincipal):
            await log_access(
                conn,
                tenant_id=tenant_id,
                process_id=process["id"],
                process_code=canonical_code,
                action="v1_read_sources",
            )
        claim_evidence = (
            await load_summary_claim_evidence(conn, summary_id=summary_id)
            if summary_id is not None
            else []
        )
        if int(process["secrecy_level"] or 0) > 0:
            claim_evidence = []
        elif not claim_evidence_is_publishable(
            summary_structured_output,
            claim_evidence,
            process=process,
        ):
            summary_id = None
            claim_evidence = []
        sources = await _load_sources(
            conn,
            process_id=process["id"],
            version_id=process["current_version_id"],
            summary_id=summary_id,
        )
        flags = {"secrecy": int(process["secrecy_level"] or 0) > 0}
        flags.update(
            await _load_attachment_flags(
                conn,
                process_id=process["id"],
                version_id=process["current_version_id"],
            )
        )
        return {
            "cnj": canonical_code,
            "source_updated_at": process["updated_at"],
            "sources": sources,
            "claim_evidence": claim_evidence,
            "flags": flags,
        }


@router.get("/v1/processos")
async def list_processos_v1(request: Request, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    principal = await principal_from_request_unscoped(request)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH combined AS (
                SELECT
                    p.code,
                    p.court,
                    p.class_name,
                    p.updated_at,
                    r.status AS acquisition_status,
                    r.created_at AS requested_at,
                    r.completed_at AS last_acquisition_at,
                    COALESCE(r.completed_at, r.created_at, p.updated_at) AS sort_time
                FROM tenant_processes tp
                JOIN processes p ON p.id = tp.process_id
                LEFT JOIN tenant_judit_requests r ON r.tenant_id = tp.tenant_id AND r.process_code = p.code
                WHERE tp.tenant_id = $1

                UNION

                SELECT
                    r.process_code AS code,
                    NULL AS court,
                    NULL AS class_name,
                    r.created_at AS updated_at,
                    r.status AS acquisition_status,
                    r.created_at AS requested_at,
                    r.completed_at AS last_acquisition_at,
                    r.created_at AS sort_time
                FROM tenant_judit_requests r
                LEFT JOIN processes p ON p.code = r.process_code
                LEFT JOIN tenant_processes tp ON tp.process_id = p.id AND tp.tenant_id = r.tenant_id
                WHERE r.tenant_id = $1 AND tp.process_id IS NULL
            )
            SELECT code, court, class_name, updated_at, acquisition_status, requested_at, last_acquisition_at
            FROM combined
            ORDER BY sort_time DESC, code ASC
            LIMIT $2 OFFSET $3
            """,
            principal.tenant_id,
            limit,
            offset,
        )

        total = await conn.fetchval(
            """
            WITH combined AS (
                SELECT p.code
                FROM tenant_processes tp
                JOIN processes p ON p.id = tp.process_id
                WHERE tp.tenant_id = $1

                UNION

                SELECT r.process_code AS code
                FROM tenant_judit_requests r
                WHERE r.tenant_id = $1
            )
            SELECT COUNT(*) FROM combined
            """,
            principal.tenant_id,
        )

        await log_access(
            conn,
            tenant_id=principal.tenant_id,
            process_id=None,
            process_code="*",
            action="v1_list_processes",
            metadata={"limit": limit, "offset": offset},
        )

        items = [
            {
                "code": r["code"],
                "court": r["court"] or "Não informado",
                "class_name": r["class_name"] or "Não informada",
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "acquisition_status": r["acquisition_status"] or "completed",
                "requested_at": r["requested_at"].isoformat() if r["requested_at"] else None,
                "last_acquisition_at": r["last_acquisition_at"].isoformat() if r["last_acquisition_at"] else None,
            }
            for r in rows
        ]

        return {
            "items": items,
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }


@router.get("/v1/processos/{code}/status")
@router.get("/v1/processos/{code}/precheck")
async def get_processo_status_v1(code: str, request: Request) -> dict:
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

    principal = await principal_from_request_unscoped(request)
    operation = request.query_params.get("operation")
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        status_result = await classify_cnj_status(
            conn,
            tenant_id=principal.tenant_id,
            code=canonical_code,
            operation=operation,
        )
        process = await get_authorized_process(
            conn, tenant_id=principal.tenant_id, code=canonical_code
        )
        await log_access(
            conn,
            tenant_id=principal.tenant_id,
            process_id=process["id"] if process else None,
            process_code=canonical_code,
            action="v1_precheck_process",
            metadata={"classification": status_result.classification},
        )
    return status_result.to_dict()


@router.post("/v1/processos/{code}/authorizations", status_code=201)
async def create_processo_authorization_v1(code: str, request: Request) -> dict:
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

    principal = await principal_from_request_unscoped(request)
    body_data: dict[str, Any] = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body_bytes = await request.body()
            if body_bytes:
                parsed = loads_strict_json(body_bytes)
                if isinstance(parsed, dict):
                    body_data = parsed
        except Exception:
            body_data = {}

    operation = str(body_data.get("operation", "refresh")).strip().lower()
    source = str(body_data.get("source", "judit")).strip().lower()
    if operation not in {"refresh", "acquisition"}:
        raise HTTPException(status_code=400, detail="operation must be 'refresh' or 'acquisition'")
    if source != "judit":
        raise HTTPException(status_code=400, detail="source must be 'judit'")

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        auth = await create_authorization(
            conn,
            tenant_id=principal.tenant_id,
            code=canonical_code,
            source=source,
            operation=operation,
        )
        process = await get_authorized_process(
            conn, tenant_id=principal.tenant_id, code=canonical_code
        )
        await log_access(
            conn,
            tenant_id=principal.tenant_id,
            process_id=process["id"] if process else None,
            process_code=canonical_code,
            action="v1_create_acquisition_authorization",
            metadata={"authorization_id": str(auth.id), "operation": operation},
        )
    return {
        "token": auth.token,
        "code": canonical_code,
        "source": auth.source,
        "operation": auth.operation,
        "expires_at": auth.expires_at.isoformat(),
        "created_at": auth.created_at.isoformat(),
    }


@router.get("/v1/auth/verificar")
@router.get("/auth/verificar")
async def v1_verify_auth(request: Request) -> dict[str, Any]:
    principal = await principal_from_request_unscoped(request)
    return {"ok": True, "authenticated": True, "tenant_id": str(principal.tenant_id)}


@router.get("/v1/capacidades")
@router.get("/v1/capabilities")
@router.get("/capacidades")
@router.get("/capabilities")
async def v1_get_capabilities(request: Request) -> dict[str, Any]:
    authenticated = False
    tenant_id = None
    try:
        principal = await principal_from_request_unscoped(request)
        authenticated = True
        tenant_id = str(principal.tenant_id)
    except Exception:
        authenticated = False
        tenant_id = None

    dj_enabled, dj_reason = datajud_activation_status()
    dj_configured = dj_enabled

    return {
        "authenticated": authenticated,
        "tenant_id": tenant_id,
        "default_source": "judit",
        "sources": {
            "judit": {
                "name": "Judit",
                "configured": True,
                "enabled": True,
                "experimental": False,
                "description": "Fonte principal para busca completa de processos, peças, movimentações e resumos jurídicos.",
                "operations": ["precheck", "request", "authorization", "read", "history"],
            },
            "datajud": {
                "name": "DataJud",
                "configured": dj_configured,
                "enabled": dj_enabled,
                "experimental": True,
                "description": "Base pública do CNJ (metadados textuais). Não substitui a Judit; não realiza aquisição de peças nem resumos judiciais automatizados.",
                "operations": ["metadata_lookup"] if dj_enabled else [],
                "reason_unavailable": dj_reason,
            },
        },
    }


@router.get("/v1/processos/{code}/datajud")
@router.get("/processos/{code}/datajud")
async def v1_reject_synchronous_datajud_metadata(code: str, request: Request):
    await reject_synchronous_datajud_lookup(request, code)


@router.post("/v1/processos/{code}/datajud", status_code=202)
@router.post("/processos/{code}/datajud", status_code=202)
async def v1_submit_datajud_metadata(code: str, request: Request):
    return await submit_datajud_lookup(request, code)


@router.get("/v1/processos/datajud/history")
@router.get("/processos/datajud/history")
async def v1_datajud_history(request: Request):
    return await list_datajud_history(request)


@router.post("/v1/processos/datajud/jobs", status_code=202)
@router.post("/processos/datajud/jobs", status_code=202)
async def v1_enqueue_datajud_job(body: DataJudJobSubmission, request: Request):
    return await submit_datajud_lookup(request, body.code)


@router.post("/v1/processos/datajud/jobs/status")
@router.post("/processos/datajud/jobs/status")
async def v1_poll_datajud_job(body: DataJudJobStatusRequest, request: Request):
    return await get_datajud_lookup(request, body.code, body.job_id)


@router.get("/v1/processos/{code}/datajud/jobs/{job_id}")
@router.get("/processos/{code}/datajud/jobs/{job_id}")
async def v1_read_datajud_metadata_job(code: str, job_id: UUID, request: Request):
    return await get_datajud_lookup(request, code, job_id)

