from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Request

from app.api_key_middleware import ApiKeySecurityMiddleware
from app.api_key_auth import authorization_header, bearer_credential
from app.api_v1 import router as api_v1_router
from app.auth import configured_bearer_tokens, tenant_from_request
from app.browser_origins import BrowserProcessCorsMiddleware
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
from app.db import create_pool
from app.frontend import router as frontend_router
from app.http_auth_config import validate_http_auth_config
from app.http_limits import InboundPostBodyLimitMiddleware, judit_webhook_max_body_bytes
from app.json_utils import decode_json_object, loads_strict_json
from app.judit import normalize_cnj, parse_event
from app.judit_client import JuditRequestError
from app.observability import (
    collect_operational_metrics,
    list_failed_summaries,
    operational_thresholds,
)
from app.process_query_safety import (
    AmbiguousFailureError,
    AuthorizationAlreadyConsumedError,
    AuthorizationExpiredError,
    InvalidAuthorizationError,
    RefreshRequiresConfirmationError,
    classify_cnj_status,
    create_authorization,
)
from app.process_requests import grant_request_tenants, request_process
from app.processes import get_authorized_process, log_access, stage_version
from app.queue import enqueue
from app.security_headers import SecurityResponseHeadersMiddleware
from app.summary_policy import is_restricted_local_summary, restricted_public_header
from app.tenancy import configured_webhook_tenant, validate_carteira_seed
from app.webhook_security import (
    JuditWebhookSecretRedactionMiddleware,
    webhook_token_from_scope,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    judit_webhook_max_body_bytes()
    operational_thresholds()
    validate_http_auth_config()
    app.state.bearer_tokens = configured_bearer_tokens()
    app.state.webhook_tenant_id = configured_webhook_tenant()
    validate_carteira_seed()
    app.state.pool = await create_pool(database_url)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(title="Rpy", lifespan=lifespan)
app.add_middleware(ApiKeySecurityMiddleware)
app.add_middleware(InboundPostBodyLimitMiddleware)
app.add_middleware(JuditWebhookSecretRedactionMiddleware)
app.add_middleware(SecurityResponseHeadersMiddleware)
app.add_middleware(BrowserProcessCorsMiddleware)
app.include_router(api_v1_router)
app.include_router(frontend_router)


def _valid_webhook_token(token: str) -> bool:
    expected = os.environ.get("JUDIT_WEBHOOK_TOKEN", "")
    return bool(expected) and hmac.compare_digest(token, expected)


def _valid_ops_request(request: Request) -> bool:
    expected = os.environ.get("RPY_OPS_TOKEN", "")
    authorization = authorization_header(request) or ""
    scheme, _, supplied = authorization.partition(" ")
    supplied = supplied.strip()
    if not expected or scheme.casefold() != "bearer":
        return False
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _json_value(value, *, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _summary_status(summary, job_status: str | None, cached_response: bool) -> str:
    if summary is not None:
        return "available"
    if cached_response:
        return "not_generated"
    if job_status in {"pending", "processing"}:
        return "processing"
    if job_status == "dead":
        return "unavailable"
    return "not_generated"


@app.get("/health")
@app.get("/healthz")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/ready")
@app.get("/readyz")
async def ready(request: Request) -> dict[str, bool]:
    pool: asyncpg.Pool = request.app.state.pool

    async def _probe() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    try:
        await asyncio.wait_for(_probe(), timeout=2.0)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
        raise HTTPException(status_code=503, detail="database unavailable") from None
    return {"ok": True}


@app.get("/auth/verify")
async def verify_auth(request: Request) -> dict[str, Any]:
    tenant_id = tenant_from_request(request)
    return {"ok": True, "authenticated": True, "tenant_id": str(tenant_id)}


@app.get("/capabilities")
async def get_capabilities(request: Request) -> dict[str, Any]:
    authenticated = False
    tenant_id = None
    try:
        if bearer_credential(request):
            tid = tenant_from_request(request)
            authenticated = True
            tenant_id = str(tid)
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


@app.get("/processes/{code}/datajud")
async def reject_synchronous_datajud_metadata(code: str, request: Request):
    await reject_synchronous_datajud_lookup(request, code)


@app.post("/processes/{code}/datajud", status_code=202)
async def submit_datajud_metadata(code: str, request: Request):
    return await submit_datajud_lookup(request, code)


@app.get("/processes/{code}/datajud/jobs/{job_id}")
async def read_datajud_metadata_job(code: str, job_id: str, request: Request):
    from uuid import UUID

    try:
        parsed_job_id = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="DataJud job not found") from None
    return await get_datajud_lookup(request, code, parsed_job_id)


@app.get("/processes/datajud/history")
async def read_datajud_history(request: Request):
    return await list_datajud_history(request)


@app.post("/processes/datajud/jobs", status_code=202)
async def enqueue_datajud_job(body: DataJudJobSubmission, request: Request):
    return await submit_datajud_lookup(request, body.code)


@app.post("/processes/datajud/jobs/status")
async def poll_datajud_job(body: DataJudJobStatusRequest, request: Request):
    return await get_datajud_lookup(request, body.code, body.job_id)


@app.get("/ops/metrics")
async def operational_metrics(request: Request) -> dict:
    if not _valid_ops_request(request):
        raise HTTPException(status_code=404, detail="not found")
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        return await collect_operational_metrics(conn)


@app.get("/ops/failed-summaries")
async def failed_summaries(request: Request) -> dict:
    if not _valid_ops_request(request):
        raise HTTPException(status_code=404, detail="not found")
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        return {"failed_summaries": await list_failed_summaries(conn)}


@app.get("/processes/{code}/status")
@app.get("/processes/{code}/precheck")
async def get_process_status(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

    operation = request.query_params.get("operation")
    async with request.app.state.pool.acquire() as conn:
        status_result = await classify_cnj_status(
            conn,
            tenant_id=tenant_id,
            code=canonical_code,
            operation=operation,
        )
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"] if process else None,
            process_code=canonical_code,
            action="precheck_process",
            metadata={"classification": status_result.classification},
        )
    return status_result.to_dict()


@app.post("/processes/{code}/authorizations", status_code=201)
async def create_process_authorization(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

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

    async with request.app.state.pool.acquire() as conn:
        auth = await create_authorization(
            conn,
            tenant_id=tenant_id,
            code=canonical_code,
            source=source,
            operation=operation,
        )
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"] if process else None,
            process_code=canonical_code,
            action="create_acquisition_authorization",
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


@app.post("/processes/{code}/request", status_code=202)
async def create_process_request(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

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

    auth_token = (
        request.headers.get("X-Acquisition-Authorization")
        or request.headers.get("X-Judit-Authorization-Token")
        or request.headers.get("X-Confirmation-Token")
        or body_data.get("authorization_token")
        or body_data.get("confirmation_token")
    )
    if auth_token:
        auth_token = str(auth_token).strip()

    force_requested = (
        request.query_params.get("force", "").lower() in {"true", "1"}
        or body_data.get("force") is True
        or body_data.get("refresh") is True
    )
    if force_requested and not auth_token:
        raise HTTPException(
            status_code=409,
            detail="force=true is not sufficient authorization; explicit authorization token is required",
        )

    try:
        result = await request_process(
            request.app.state.pool,
            tenant_id=tenant_id,
            code=canonical_code,
            authorization_token=auth_token,
            allow_refresh=force_requested,
        )
    except AmbiguousFailureError as exc:
        raise HTTPException(
            status_code=409,
            detail="acquisition blocked due to ambiguous provider failure",
        ) from exc
    except RefreshRequiresConfirmationError as exc:
        raise HTTPException(
            status_code=409,
            detail="process already consulted; refresh requires explicit confirmation authorization",
        ) from exc
    except AuthorizationAlreadyConsumedError as exc:
        raise HTTPException(
            status_code=409,
            detail="authorization token has already been consumed",
        ) from exc
    except AuthorizationExpiredError as exc:
        raise HTTPException(
            status_code=400,
            detail="authorization token has expired",
        ) from exc
    except InvalidAuthorizationError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except JuditRequestError:
        raise HTTPException(status_code=503, detail="process provider unavailable") from None

    async with request.app.state.pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"] if process else None,
            process_code=canonical_code,
            action="request_process",
            metadata={"created": result.created, "authorized": bool(auth_token)},
        )
    return {
        "code": canonical_code,
        "status": "available" if (process and not result.created) else "processing",
        "created": result.created,
    }


@app.get("/processes")
async def list_processes(
    request: Request,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    tenant_id = tenant_from_request(request)
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
            tenant_id,
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
            tenant_id,
        )

        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=None,
            process_code="*",
            action="list_processes",
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


@app.get("/processes/{code}")
async def get_process_summary(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        if process is None:
            raise HTTPException(status_code=404, detail="process not found")
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"],
            process_code=canonical_code,
            action="read_process_summary",
        )
        is_restricted = int(process["secrecy_level"] or 0) > 0
        summary = await conn.fetchrow(
            """
            SELECT id, markdown, structured_output, validation, model, prompt_version,
                   generation_ms, created_at
            FROM process_summaries
            WHERE process_id = $1
              AND version_id = $2
              AND COALESCE((validation->>'passed')::boolean, false) = true
            """,
            process["id"],
            process["current_version_id"],
        )
        if summary is not None:
            if is_restricted:
                if not is_restricted_local_summary(summary, process=process):
                    summary = None
            else:
                claim_evidence = await load_summary_claim_evidence(
                    conn, summary_id=summary["id"]
                )
                structured_output = (
                    decode_json_object(
                        summary["structured_output"],
                        label="summary structured output",
                    )
                    if summary["structured_output"] is not None
                    else None
                )
                if not claim_evidence_is_publishable(
                    structured_output,
                    claim_evidence,
                    process=process,
                ):
                    summary = None
        steps = []
        summary_job_status = None
        cached_response = False
        if process["current_version_id"] is not None:
            if not is_restricted:
                steps = await conn.fetch(
                    """
                    SELECT step_number, occurred_at, title, text
                    FROM process_steps
                    WHERE process_id = $1 AND version_id = $2
                    ORDER BY occurred_at DESC NULLS LAST, step_number DESC
                    LIMIT 20
                    """,
                    process["id"],
                    process["current_version_id"],
                )
            cached_response = bool(
                await conn.fetchval(
                    "SELECT source_cached_response FROM process_versions WHERE id = $1 AND process_id = $2",
                    process["current_version_id"],
                    process["id"],
                )
            )
            summary_job_status = await conn.fetchval(
                """
                SELECT status::text FROM jobs
                WHERE task_name = 'generate_process_summary' AND idempotency_key = $1
                ORDER BY created_at DESC LIMIT 1
                """,
                f"summary:{process['current_version_id']}",
            )

    summary_data = dict(summary) if summary else None
    if summary_data is not None:
        summary_data.pop("id", None)
        summary_data.pop("structured_output", None)
        summary_data["validation"] = decode_json_object(
            summary_data.get("validation"), label="summary validation"
        )

    ia_summary = summary_data["markdown"] if summary_data else None
    if is_restricted:
        parties = []
        subjects = []
        header = restricted_public_header(
            _json_value(process["header"], fallback={})
        )
    else:
        parties = _json_value(process["parties"], fallback=[])
        subjects = _json_value(process["subjects"], fallback=[])
        header = _json_value(process["header"], fallback={})

    return {
        "code": process["code"],
        "class_name": process["class_name"],
        "court": None if is_restricted else process["court"],
        "parties": parties if isinstance(parties, list) else [],
        "subjects": subjects if isinstance(subjects, list) else [],
        "header": header if isinstance(header, dict) else {},
        "updated_at": process["updated_at"],
        "summary_status": _summary_status(summary_data, summary_job_status, cached_response),
        "summary": summary_data,
        "iaSummary": ia_summary,
        "recent_steps": [dict(step) for step in steps],
    }


async def _record_delivery(conn: asyncpg.Connection, event) -> bool:
    if not event.callback_id:
        return True
    inserted = await conn.fetchval(
        """
        INSERT INTO judit_deliveries (callback_id, request_id, event_type, raw_payload)
        VALUES ($1, $2, $3, $4::jsonb)
        ON CONFLICT (callback_id) DO NOTHING
        RETURNING callback_id
        """,
        event.callback_id,
        event.request_id,
        event.event_type,
        json.dumps(event.raw),
    )
    return inserted is not None


async def _record_request_completion(conn: asyncpg.Connection, request_id: str) -> None:
    await conn.execute(
        "INSERT INTO judit_request_completions (request_id) VALUES ($1) ON CONFLICT (request_id) DO NOTHING",
        request_id,
    )
    await conn.execute(
        "UPDATE tenant_judit_requests SET completed_at = COALESCE(completed_at, NOW()) WHERE judit_request_id = $1",
        request_id,
    )


async def _request_was_completed(conn: asyncpg.Connection, request_id: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM judit_request_completions WHERE request_id = $1)",
            request_id,
        )
    )


async def _enqueue_finalize(
    conn: asyncpg.Connection, *, request_id: str, idempotency_key: str
) -> None:
    await enqueue(
        conn,
        task_name="finalize_judit_request",
        payload={"request_id": request_id},
        idempotency_key=idempotency_key,
    )


@app.post("/webhooks/judit/{token}")
async def judit_webhook(token: str, request: Request) -> dict[str, bool]:
    supplied_token = webhook_token_from_scope(request.scope, token)
    if not _valid_webhook_token(supplied_token):
        raise HTTPException(status_code=404, detail="not found")

    try:
        body = loads_strict_json(await request.body())
        event = parse_event(body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid payload") from None

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await _record_delivery(conn, event):
                return {"ok": True}

            if event.is_lawsuit_response:
                source_id = event.response_id or event.callback_id
                process_id, _ = await stage_version(
                    conn,
                    code=str(event.code),
                    source_request_id=source_id,
                    cached_response=event.cached_response,
                    payload=event.raw,
                    judit_request_id=event.request_id,
                    judit_response_id=event.response_id,
                    judit_callback_id=event.callback_id,
                    tenant_id=getattr(request.app.state, "webhook_tenant_id", None),
                )
                if event.request_id:
                    await grant_request_tenants(
                        conn, request_id=str(event.request_id), process_id=process_id
                    )
                if await _request_was_completed(conn, str(event.request_id)):
                    await _enqueue_finalize(
                        conn,
                        request_id=str(event.request_id),
                        idempotency_key=f"judit-finalize-repair:{event.request_id}:{source_id}",
                    )
            elif event.request_completed and event.request_id:
                await _record_request_completion(conn, event.request_id)
                await _enqueue_finalize(
                    conn,
                    request_id=event.request_id,
                    idempotency_key=f"judit-finalize:{event.request_id}",
                )
    return {"ok": True}
