from __future__ import annotations

from datetime import timedelta

import asyncpg

from app.queue import enqueue


async def enqueue_due_tracking_reconciliations(
    conn: asyncpg.Connection,
    *,
    stale_after: timedelta,
    limit: int,
) -> int:
    if stale_after.total_seconds() <= 0:
        raise ValueError("tracking stale_after must be greater than zero")
    if limit <= 0:
        raise ValueError("tracking reconcile limit must be greater than zero")

    rows = await conn.fetch(
        """
        SELECT id
        FROM judit_trackings
        WHERE status = 'active'
          AND GREATEST(
                COALESCE(last_event_at, created_at),
                COALESCE(last_reconciled_at, created_at)
              ) < NOW() - $1::interval
        ORDER BY GREATEST(
                   COALESCE(last_event_at, created_at),
                   COALESCE(last_reconciled_at, created_at)
                 ) ASC,
                 id ASC
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
        await conn.execute(
            "UPDATE judit_trackings SET last_reconciled_at=NOW(), updated_at=NOW() WHERE id=$1",
            row["id"],
        )
        await enqueue(
            conn,
            task_name="refresh_judit_tracking",
            payload={"refresh_id": str(refresh["id"])},
            max_attempts=1,
            idempotency_key=f"judit-tracking-refresh:{refresh['id']}",
        )
        created += 1
    return created
