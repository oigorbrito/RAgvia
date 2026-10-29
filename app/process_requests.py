from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.db import create_pool
from app.judit_client import (
    JuditRequestError,
    create_lawsuit_request,
    reserved_judit_acquisition_call,
)
from app.provider_policy import provider_one_shot_mode_enabled, provider_one_shot_run_id
from app.process_query_safety import (
    AmbiguousFailureError,
    InvalidAuthorizationError,
    RefreshRequiresConfirmationError,
    consume_authorization,
)
from app.public_lifecycle import (
    link_summary_request_to_acquisition,
    transition_requests_for_acquisition,
    transition_summary_request,
)
from app.queue import enqueue
from app.tasks import PermanentTaskError


@dataclass(frozen=True, slots=True)
class ProcessRequestResult:
    request_id: str
    created: bool
    budget_exhausted: bool = False


def configured_judit_provider_call_limit() -> int:
    raw = os.getenv("JUDIT_MAX_PROVIDER_CALLS", "100").strip()
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("JUDIT_MAX_PROVIDER_CALLS must be an integer from 1 to 100") from exc
    if not 1 <= limit <= 100:
        raise ValueError("JUDIT_MAX_PROVIDER_CALLS must be an integer from 1 to 100")
    one_shot = provider_one_shot_mode_enabled()
    if one_shot and limit != 1:
        raise ValueError("PROVIDER_ONE_SHOT_MODE requires JUDIT_MAX_PROVIDER_CALLS=1")
    if one_shot:
        provider_one_shot_run_id()
    if limit == 1 and not one_shot:
        raise ValueError("JUDIT_MAX_PROVIDER_CALLS=1 requires PROVIDER_ONE_SHOT_MODE=true")
    return limit


async def _enqueue_acquisition(conn: asyncpg.Connection, *, request_row_id: UUID, attempt: int) -> None:
    await enqueue(
        conn,
        task_name="request_judit_process",
        payload={"tenant_request_id": str(request_row_id)},
        max_attempts=1,
        idempotency_key=f"judit-acquire:{request_row_id}:{attempt}",
    )


async def _link_public_request(
    conn: asyncpg.Connection,
    *,
    public_summary_request_id: UUID | None,
    tenant_id: UUID,
    code: str,
    tenant_judit_request_id: UUID,
    already_fetching: bool,
) -> None:
    if public_summary_request_id is None:
        return
    await link_summary_request_to_acquisition(
        conn,
        request_id=public_summary_request_id,
        tenant_id=tenant_id,
        process_code=code,
        tenant_judit_request_id=tenant_judit_request_id,
    )
    if already_fetching:
        await transition_requests_for_acquisition(
            conn,
            tenant_judit_request_id=tenant_judit_request_id,
            status="fetching",
        )


async def request_process(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    code: str,
    public_summary_request_id: UUID | None = None,
    authorization_token: str | None = None,
    allow_refresh: bool = False,
) -> ProcessRequestResult:
    """Durably register a tenant/CNJ acquisition without provider I/O.

    Explicit provider rejections may be retried by a later user request. An
    ambiguous transport/provider failure is never retried automatically because
    the provider may already have accepted a paid asynchronous request.

    When a public summary request is supplied, it is linked atomically to the
    shared tenant/CNJ acquisition. Multiple public jobs can therefore reuse one
    Judit request without duplicating paid provider work.
    """
    max_provider_calls = configured_judit_provider_call_limit()
    one_shot_run_id = provider_one_shot_run_id()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, judit_request_id, status, attempt_number, max_provider_calls, provider_calls_reserved, created_at, completed_at FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2 FOR UPDATE",
                tenant_id,
                code,
            )

            # Invariant: Never repeat failed_ambiguous automatically
            if existing is not None and existing["status"] == "failed_ambiguous":
                raise AmbiguousFailureError(
                    f"process {code} acquisition is blocked due to an ambiguous provider failure"
                )

            # Check if process already exists for this tenant
            process_exists = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM tenant_processes tp
                    JOIN processes p ON p.id = tp.process_id
                    WHERE tp.tenant_id = $1 AND p.code = $2
                )
                """,
                tenant_id,
                code,
            )

            if one_shot_run_id is not None:
                global_reservation = await conn.fetchrow(
                    "SELECT tenant_request_id FROM judit_one_shot_call_reservations WHERE run_id=$1",
                    one_shot_run_id,
                )
                wants_new_acquisition = not process_exists or bool(authorization_token)
                if (
                    global_reservation is not None
                    and wants_new_acquisition
                    and (
                        global_reservation["tenant_request_id"] is None
                        or existing is None
                        or existing["id"] != global_reservation["tenant_request_id"]
                    )
                ):
                    if public_summary_request_id is not None:
                        public_scope = await conn.fetchrow(
                            "SELECT tenant_id, process_code FROM public_summary_requests WHERE id=$1 FOR UPDATE",
                            public_summary_request_id,
                        )
                        if (
                            public_scope is None
                            or public_scope["tenant_id"] != tenant_id
                            or public_scope["process_code"] != code
                        ):
                            raise PermanentTaskError("public summary request scope mismatch")
                        await transition_summary_request(
                            conn,
                            request_id=public_summary_request_id,
                            status="source_unavailable",
                            error_code="provider_call_budget_exhausted",
                        )
                    return ProcessRequestResult(
                        request_id="",
                        created=False,
                        budget_exhausted=True,
                    )

            # Never consume a refresh authorization or enqueue another Judit call
            # after this tenant/process budget has been fully reserved.
            if (
                existing is not None
                and (
                    int(existing["provider_calls_reserved"])
                    >= int(existing["max_provider_calls"])
                    or (
                        one_shot_run_id is not None
                        and int(existing["provider_calls_reserved"]) > 0
                    )
                )
                and (
                    existing["status"] == "failed_retryable"
                    or (process_exists and authorization_token)
                )
            ):
                if public_summary_request_id is not None:
                    await _link_public_request(
                        conn,
                        public_summary_request_id=public_summary_request_id,
                        tenant_id=tenant_id,
                        code=code,
                        tenant_judit_request_id=existing["id"],
                        already_fetching=bool(existing["judit_request_id"]),
                    )
                    await transition_requests_for_acquisition(
                        conn,
                        tenant_judit_request_id=existing["id"],
                        status="source_unavailable",
                        error_code="provider_call_budget_exhausted",
                    )
                return ProcessRequestResult(
                    request_id=str(existing["judit_request_id"] or ""),
                    created=False,
                    budget_exhausted=True,
                )

            # If an authorization token was explicitly provided, validate and consume it first
            if authorization_token:
                if process_exists:
                    await consume_authorization(
                        conn,
                        token=authorization_token,
                        tenant_id=tenant_id,
                        code=code,
                        source="judit",
                        operation="refresh",
                        request_id=existing["id"] if existing else None,
                    )
                else:
                    try:
                        await consume_authorization(
                            conn,
                            token=authorization_token,
                            tenant_id=tenant_id,
                            code=code,
                            source="judit",
                            operation="acquisition",
                            request_id=existing["id"] if existing else None,
                        )
                    except InvalidAuthorizationError:
                        await consume_authorization(
                            conn,
                            token=authorization_token,
                            tenant_id=tenant_id,
                            code=code,
                            source="judit",
                            operation="refresh",
                            request_id=existing["id"] if existing else None,
                        )

            # If process already exists for this tenant: this is a refresh operation
            if process_exists:
                if not authorization_token:
                    if public_summary_request_id is not None and existing is not None:
                        await _link_public_request(
                            conn,
                            public_summary_request_id=public_summary_request_id,
                            tenant_id=tenant_id,
                            code=code,
                            tenant_judit_request_id=existing["id"],
                            already_fetching=bool(existing["judit_request_id"]),
                        )
                    if allow_refresh:
                        raise RefreshRequiresConfirmationError(
                            "process already exists in history; refresh requires explicit confirmation authorization"
                        )
                    return ProcessRequestResult(
                        request_id=str(existing["judit_request_id"] if existing else ""),
                        created=False,
                    )

                if existing is not None:
                    await conn.execute(
                        """
                        INSERT INTO tenant_judit_request_history (
                            tenant_request_id,
                            tenant_id,
                            process_code,
                            judit_request_id,
                            status,
                            attempt_number,
                            created_at,
                            completed_at
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        existing["id"],
                        tenant_id,
                        code,
                        existing["judit_request_id"],
                        existing["status"],
                        existing["attempt_number"],
                        existing["created_at"],
                        existing["completed_at"],
                    )
                    await conn.execute(
                        """
                        UPDATE tenant_judit_requests
                        SET status = 'processing',
                            judit_request_id = NULL,
                            completed_at = NULL,
                            attempt_number = 1,
                            created_at = NOW()
                        WHERE id = $1
                        """,
                        existing["id"],
                    )
                    request_row_id = existing["id"]
                else:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO tenant_judit_requests (
                            tenant_id, process_code, status, attempt_number, max_provider_calls
                        )
                        VALUES ($1, $2, 'processing', 1, $3)
                        ON CONFLICT (tenant_id, process_code) DO UPDATE
                        SET status = 'processing',
                            judit_request_id = NULL,
                            completed_at = NULL,
                            attempt_number = 1,
                            max_provider_calls = GREATEST(
                                tenant_judit_requests.provider_calls_reserved,
                                LEAST(tenant_judit_requests.max_provider_calls, EXCLUDED.max_provider_calls)
                            ),
                            created_at = NOW()
                        RETURNING id
                        """,
                        tenant_id,
                        code,
                        max_provider_calls,
                    )
                    request_row_id = row["id"]

                await _link_public_request(
                    conn,
                    public_summary_request_id=public_summary_request_id,
                    tenant_id=tenant_id,
                    code=code,
                    tenant_judit_request_id=request_row_id,
                    already_fetching=False,
                )
                await _enqueue_acquisition(conn, request_row_id=request_row_id, attempt=1)
                return ProcessRequestResult(request_id="", created=True)

            # If an acquisition is already processing, don't duplicate paid work
            if existing is not None and existing["status"] == "processing":
                await _link_public_request(
                    conn,
                    public_summary_request_id=public_summary_request_id,
                    tenant_id=tenant_id,
                    code=code,
                    tenant_judit_request_id=existing["id"],
                    already_fetching=bool(existing["judit_request_id"]),
                )
                return ProcessRequestResult(
                    request_id=str(existing["judit_request_id"] or ""), created=False
                )

            if existing is not None:
                if (
                    existing["status"] == "failed_retryable"
                    and int(existing["attempt_number"]) < int(existing["max_provider_calls"])
                    and int(existing["provider_calls_reserved"]) < int(existing["max_provider_calls"])
                ):
                    attempt = int(existing["attempt_number"]) + 1
                    await conn.execute(
                        "UPDATE tenant_judit_requests SET status='processing', attempt_number=$2 WHERE id=$1",
                        existing["id"],
                        attempt,
                    )
                    await _link_public_request(
                        conn,
                        public_summary_request_id=public_summary_request_id,
                        tenant_id=tenant_id,
                        code=code,
                        tenant_judit_request_id=existing["id"],
                        already_fetching=False,
                    )
                    await _enqueue_acquisition(conn, request_row_id=existing["id"], attempt=attempt)
                    return ProcessRequestResult(request_id="", created=True)

                await _link_public_request(
                    conn,
                    public_summary_request_id=public_summary_request_id,
                    tenant_id=tenant_id,
                    code=code,
                    tenant_judit_request_id=existing["id"],
                    already_fetching=bool(existing["judit_request_id"]),
                )
                return ProcessRequestResult(
                    request_id=str(existing["judit_request_id"] or ""), created=False
                )

            row = await conn.fetchrow(
                """
                INSERT INTO tenant_judit_requests (
                    tenant_id, process_code, status, attempt_number, max_provider_calls
                )
                VALUES ($1, $2, 'processing', 1, $3)
                ON CONFLICT (tenant_id, process_code) DO NOTHING
                RETURNING id
                """,
                tenant_id,
                code,
                max_provider_calls,
            )
            if row is not None:
                request_row_id = row["id"]
                created = True
            else:
                row = await conn.fetchrow(
                    "SELECT id, judit_request_id, status FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2 FOR UPDATE",
                    tenant_id,
                    code,
                )
                request_row_id = row["id"]
                created = False

            await _link_public_request(
                conn,
                public_summary_request_id=public_summary_request_id,
                tenant_id=tenant_id,
                code=code,
                tenant_judit_request_id=request_row_id,
                already_fetching=bool(row["judit_request_id"]) if not created else False,
            )
            if created:
                await _enqueue_acquisition(conn, request_row_id=request_row_id, attempt=1)
            return ProcessRequestResult(
                request_id=str(row["judit_request_id"] or "") if not created else "",
                created=created,
            )


async def _reconcile_tenant_access(
    conn: asyncpg.Connection, *, request_id: str, tenant_id: UUID
) -> None:
    process_id = await conn.fetchval(
        """
        SELECT process_id
        FROM process_versions
        WHERE judit_request_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        request_id,
    )
    if process_id is not None:
        await conn.execute(
            "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            tenant_id,
            process_id,
        )


async def request_judit_process_task(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        tenant_request_id = UUID(str(payload["tenant_request_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("invalid tenant process request payload") from exc

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    one_shot_mode = provider_one_shot_mode_enabled()
    one_shot_run_id = provider_one_shot_run_id()
    if one_shot_mode:
        configured_judit_provider_call_limit()

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        budget_exhausted = False
        run_budget_exhausted = False
        request_missing = False
        async with pool.acquire() as conn:
            async with conn.transaction():
                request_row = await conn.fetchrow(
                    "SELECT tenant_id, process_code, judit_request_id, status, max_provider_calls, provider_calls_reserved FROM tenant_judit_requests WHERE id=$1 FOR UPDATE",
                    tenant_request_id,
                )
                if request_row is None:
                    request_missing = True
                elif request_row["judit_request_id"]:
                    return {"status": str(request_row["status"])}
                elif request_row["status"] != "processing":
                    return {"status": str(request_row["status"])}
                else:
                    if one_shot_mode and int(request_row["max_provider_calls"]) != 1:
                        raise PermanentTaskError("Judit one-shot budget is not persisted as one")
                    if int(request_row["provider_calls_reserved"]) >= int(request_row["max_provider_calls"]):
                        budget_exhausted = True
                    elif one_shot_run_id is not None:
                        global_reservation = await conn.fetchrow(
                            """
                            INSERT INTO judit_one_shot_call_reservations (
                                run_id, tenant_request_id
                            ) VALUES ($1, $2)
                            ON CONFLICT (run_id) DO NOTHING
                            RETURNING run_id
                            """,
                            one_shot_run_id,
                            tenant_request_id,
                        )
                        run_budget_exhausted = global_reservation is None
                        if run_budget_exhausted:
                            await conn.execute(
                                "UPDATE tenant_judit_requests SET status='failed_retryable' WHERE id=$1 AND status='processing'",
                                tenant_request_id,
                            )
                            await transition_requests_for_acquisition(
                                conn,
                                tenant_judit_request_id=tenant_request_id,
                                status="source_unavailable",
                                error_code="provider_call_budget_exhausted",
                            )
                    if not budget_exhausted and not run_budget_exhausted:
                        reserved_row = await conn.fetchrow(
                            """
                            UPDATE tenant_judit_requests
                            SET provider_calls_reserved = provider_calls_reserved + 1,
                                last_provider_call_reserved_at = NOW()
                            WHERE id = $1
                              AND judit_request_id IS NULL
                              AND provider_calls_reserved < max_provider_calls
                            RETURNING tenant_id, process_code, status
                            """,
                            tenant_request_id,
                        )
                        if reserved_row is None:
                            raise PermanentTaskError("Judit provider call reservation failed")
                        request_row = reserved_row
                        await transition_requests_for_acquisition(
                            conn,
                            tenant_judit_request_id=tenant_request_id,
                            status="fetching",
                        )
        if request_missing:
            raise PermanentTaskError("tenant process request not found")
        if run_budget_exhausted:
            return {"status": "call_budget_exhausted"}
        if budget_exhausted:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE tenant_judit_requests SET status='failed_ambiguous' WHERE id=$1 AND judit_request_id IS NULL AND status='processing'",
                        tenant_request_id,
                    )
            return {"status": "call_budget_exhausted"}

        try:
            with reserved_judit_acquisition_call():
                provider = await create_lawsuit_request(str(request_row["process_code"]))
        except JuditRequestError as exc:
            failure_status = "failed_retryable" if exc.retry_safe else "failed_ambiguous"
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE tenant_judit_requests SET status=$2 WHERE id=$1 AND judit_request_id IS NULL",
                        tenant_request_id,
                        failure_status,
                    )
                    await transition_requests_for_acquisition(
                        conn,
                        tenant_judit_request_id=tenant_request_id,
                        status="source_unavailable",
                        error_code="source_unavailable",
                    )
            raise PermanentTaskError("process provider request failed") from exc

        async with pool.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchrow(
                    """
                    UPDATE tenant_judit_requests
                    SET judit_request_id=$2, status='processing'
                    WHERE id=$1 AND judit_request_id IS NULL
                    RETURNING tenant_id
                    """,
                    tenant_request_id,
                    provider.request_id,
                )
                if updated is not None:
                    await _reconcile_tenant_access(
                        conn, request_id=provider.request_id, tenant_id=updated["tenant_id"]
                    )
        return {"status": "processing"}
    finally:
        await pool.close()


async def grant_request_tenants(
    conn: asyncpg.Connection, *, request_id: str, process_id: UUID
) -> None:
    await conn.execute(
        """
        INSERT INTO tenant_processes (tenant_id, process_id)
        SELECT tenant_id, $2
        FROM tenant_judit_requests
        WHERE judit_request_id = $1
        ON CONFLICT DO NOTHING
        """,
        request_id,
        process_id,
    )
