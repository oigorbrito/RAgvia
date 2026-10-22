from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.db import create_pool
from app.judit_client import (
    JuditRequestError,
    create_lawsuit_request,
    create_lawsuit_tracking,
    delete_lawsuit_tracking,
)
from app.process_requests import request_process
from app.queue import enqueue
from app.tasks import PermanentTaskError, task

DEFAULT_TRACKING_RECURRENCE_DAYS = 1
DEFAULT_TRACKING_STALE_HOURS = 36
DEFAULT_TRACKING_RECONCILE_BATCH = 25


@dataclass(frozen=True, slots=True)
class TrackingMutationResult:
    tracking_id: UUID
    status: str
    created: bool


def _positive_int(value: int, *, name: str) -> int:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


async def _enqueue_create(
    conn: asyncpg.Connection,
    *,
    tracking_id: UUID,
    attempt: int,
) -> None:
    await enqueue(
        conn,
        task_name="create_judit_tracking",
        payload={"tracking_id": str(tracking_id)},
        max_attempts=1,
        idempotency_key=f"judit-tracking-create:{tracking_id}:{attempt}",
    )


async def create_tracking(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    code: str,
    recurrence_days: int = DEFAULT_TRACKING_RECURRENCE_DAYS,
) -> TrackingMutationResult:
    recurrence_days = _positive_int(recurrence_days, name="recurrence_days")
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                """
                SELECT id, status, attempt_number
                FROM judit_trackings
                WHERE tenant_id = $1 AND process_code = $2
                FOR UPDATE
                """,
                tenant_id,
                code,
            )
            if existing is not None and existing["status"] in {"creating", "active", "deleting"}:
                return TrackingMutationResult(
                    tracking_id=existing["id"],
                    status=str(existing["status"]),
                    created=False,
                )

            if existing is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO judit_trackings (
                        tenant_id, process_code, recurrence_days, status, attempt_number
                    )
                    VALUES ($1, $2, $3, 'creating', 1)
                    RETURNING id, attempt_number
                    """,
                    tenant_id,
                    code,
                    recurrence_days,
                )
                tracking_id = row["id"]
                attempt = int(row["attempt_number"])
            else:
                attempt = int(existing["attempt_number"]) + 1
                tracking_id = existing["id"]
                await conn.execute(
                    """
                    UPDATE judit_trackings
                    SET status = 'creating',
                        provider_tracking_id = NULL,
                        recurrence_days = $2,
                        attempt_number = $3,
                        last_event_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    tracking_id,
                    recurrence_days,
                    attempt,
                )
            await _enqueue_create(conn, tracking_id=tracking_id, attempt=attempt)

    # Initial acquisition uses the existing durable request pipeline. When the
    # tenant already has a current acquisition this is intentionally a no-op.
    await request_process(pool, tenant_id=tenant_id, code=code)
    return TrackingMutationResult(tracking_id=tracking_id, status="creating", created=True)


async def list_trackings(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, process_code, provider_tracking_id, status, recurrence_days,
               last_event_at, created_at, updated_at
        FROM judit_trackings
        WHERE tenant_id = $1 AND status <> 'deleted'
        ORDER BY created_at DESC, id DESC
        """,
        tenant_id,
    )


async def get_tracking(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    tracking_id: UUID,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, tenant_id, process_code, provider_tracking_id, status,
               recurrence_days, last_event_at, created_at, updated_at
        FROM judit_trackings
        WHERE tenant_id = $1 AND id = $2 AND status <> 'deleted'
        """,
        tenant_id,
        tracking_id,
    )


async def delete_tracking(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    tracking_id: UUID,
) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, provider_tracking_id, status, attempt_number
                FROM judit_trackings
                WHERE tenant_id = $1 AND id = $2
                FOR UPDATE
                """,
                tenant_id,
                tracking_id,
            )
            if row is None or row["status"] == "deleted":
                return False
            if row["status"] == "deleting":
                return True
            attempt = int(row["attempt_number"]) + 1
            await conn.execute(
                """
                UPDATE judit_trackings
                SET status = 'deleting', attempt_number = $2, updated_at = NOW()
                WHERE id = $1
                """,
                tracking_id,
                attempt,
            )
            await enqueue(
                conn,
                task_name="delete_judit_tracking",
                payload={"tracking_id": str(tracking_id)},
                max_attempts=1,
                idempotency_key=f"judit-tracking-delete:{tracking_id}:{attempt}",
            )
            return True


async def tracking_tenant_for_provider_id(
    conn: asyncpg.Connection,
    *,
    provider_tracking_id: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, tenant_id, process_code
        FROM judit_trackings
        WHERE provider_tracking_id = $1 AND status = 'active'
        """,
        provider_tracking_id,
    )


async def mark_tracking_event(
    conn: asyncpg.Connection,
    *,
    provider_tracking_id: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE judit_trackings
        SET last_event_at = NOW(), updated_at = NOW()
        WHERE provider_tracking_id = $1 AND status = 'active'
        RETURNING id, tenant_id, process_code
        """,
        provider_tracking_id,
    )


async def complete_refresh_for_request(
    conn: asyncpg.Connection,
    *,
    request_id: str,
) -> None:
    await conn.execute(
        """
        UPDATE judit_tracking_refreshes
        SET status = 'completed', completed_at = COALESCE(completed_at, NOW())
        WHERE judit_request_id = $1 AND status IN ('pending', 'processing')
        """,
        request_id,
    )


async def grant_tracking_request_tenant(
    conn: asyncpg.Connection,
    *,
    request_id: str,
    process_id: UUID,
) -> None:
    await conn.execute(
        """
        INSERT INTO tenant_processes (tenant_id, process_id)
        SELECT jt.tenant_id, $2
        FROM judit_tracking_refreshes jtr
        JOIN judit_trackings jt ON jt.id = jtr.tracking_id
        WHERE jtr.judit_request_id = $1
        ON CONFLICT DO NOTHING
        """,
        request_id,
        process_id,
    )


async def enqueue_stale_tracking_reconciliations(
    conn: asyncpg.Connection,
    *,
    stale_after: timedelta,
    limit: int = DEFAULT_TRACKING_RECONCILE_BATCH,
) -> int:
    if stale_after.total_seconds() <= 0:
        raise ValueError("tracking stale_after must be greater than zero")
    _positive_int(limit, name="tracking reconcile limit")

    rows = await conn.fetch(
        """
        SELECT id
        FROM judit_trackings
        WHERE status = 'active'
          AND COALESCE(last_event_at, created_at) < NOW() - $1::interval
        ORDER BY COALESCE(last_event_at, created_at) ASC, id ASC
        FOR UPDATE SKIP LOCKED
        LIMIT $2
        """,
        stale_after,
        limit,
    )
    created = 0
    for row in rows:
        refresh = await conn.fetchrow(
            """
            INSERT INTO judit_tracking_refreshes (tracking_id, status, reason)
            VALUES ($1, 'pending', 'stale_tracking')
            ON CONFLICT (tracking_id)
                WHERE status IN ('pending', 'processing')
            DO NOTHING
            RETURNING id
            """,
            row["id"],
        )
        if refresh is None:
            continue
        await enqueue(
            conn,
            task_name="refresh_judit_tracking",
            payload={"refresh_id": str(refresh["id"])},
            max_attempts=1,
            idempotency_key=f"judit-tracking-refresh:{refresh['id']}",
        )
        created += 1
    return created


@task("create_judit_tracking")
async def create_judit_tracking_task(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        tracking_id = UUID(str(payload["tracking_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("invalid Judit tracking create payload") from exc
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT process_code, recurrence_days, provider_tracking_id, status
                FROM judit_trackings WHERE id = $1
                """,
                tracking_id,
            )
        if row is None:
            raise PermanentTaskError("Judit tracking not found")
        if row["provider_tracking_id"]:
            return {"status": str(row["status"]), "tracking_id": str(row["provider_tracking_id"])}
        if row["status"] != "creating":
            return {"status": str(row["status"])}
        try:
            provider = await create_lawsuit_tracking(
                str(row["process_code"]), recurrence_days=int(row["recurrence_days"])
            )
        except JuditRequestError as exc:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE judit_trackings SET status='failed', updated_at=NOW() WHERE id=$1 AND status='creating'",
                    tracking_id,
                )
            raise PermanentTaskError("Judit tracking provider request failed") from exc
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE judit_trackings
                SET provider_tracking_id=$2, status='active', updated_at=NOW()
                WHERE id=$1 AND status='creating'
                """,
                tracking_id,
                provider.tracking_id,
            )
        return {"status": "active", "tracking_id": provider.tracking_id}
    finally:
        await pool.close()


@task("delete_judit_tracking")
async def delete_judit_tracking_task(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        tracking_id = UUID(str(payload["tracking_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("invalid Judit tracking delete payload") from exc
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT provider_tracking_id, status FROM judit_trackings WHERE id=$1",
                tracking_id,
            )
        if row is None:
            raise PermanentTaskError("Judit tracking not found")
        provider_tracking_id = str(row["provider_tracking_id"] or "").strip()
        if provider_tracking_id:
            try:
                await delete_lawsuit_tracking(provider_tracking_id)
            except JuditRequestError as exc:
                raise PermanentTaskError("Judit tracking delete failed") from exc
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE judit_trackings
                SET status='deleted', provider_tracking_id=NULL, updated_at=NOW()
                WHERE id=$1
                """,
                tracking_id,
            )
        return {"status": "deleted"}
    finally:
        await pool.close()


@task("refresh_judit_tracking")
async def refresh_judit_tracking_task(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        refresh_id = UUID(str(payload["refresh_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("invalid Judit tracking refresh payload") from exc
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT jtr.id, jtr.judit_request_id, jtr.status, jt.process_code
                FROM judit_tracking_refreshes jtr
                JOIN judit_trackings jt ON jt.id = jtr.tracking_id
                WHERE jtr.id=$1 AND jt.status='active'
                """,
                refresh_id,
            )
        if row is None:
            raise PermanentTaskError("Judit tracking refresh not found")
        if row["judit_request_id"]:
            return {"status": str(row["status"]), "request_id": str(row["judit_request_id"])}
        try:
            provider = await create_lawsuit_request(str(row["process_code"]))
        except JuditRequestError as exc:
            failure_status = "failed_retryable" if exc.retry_safe else "failed_ambiguous"
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE judit_tracking_refreshes SET status=$2 WHERE id=$1 AND judit_request_id IS NULL",
                    refresh_id,
                    failure_status,
                )
            raise PermanentTaskError("Judit tracking refresh request failed") from exc
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE judit_tracking_refreshes
                SET judit_request_id=$2, status='processing'
                WHERE id=$1 AND judit_request_id IS NULL
                """,
                refresh_id,
                provider.request_id,
            )
        return {"status": "processing", "request_id": provider.request_id}
    finally:
        await pool.close()
