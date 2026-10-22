from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import asyncpg


@dataclass(frozen=True)
class OperationalThresholds:
    backup_max_age_seconds: int = 26 * 60 * 60
    queue_warn_seconds: int = 5 * 60
    queue_critical_seconds: int = 30 * 60
    stale_processing_seconds: int = 60
    dead_jobs_warn_24h: int = 1
    dead_jobs_critical_24h: int = 5
    generation_p95_warn_ms: int = 60_000
    generation_p95_critical_ms: int = 120_000


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be > 0")
    return value


def operational_thresholds() -> OperationalThresholds:
    thresholds = OperationalThresholds(
        backup_max_age_seconds=_env_int("OPS_BACKUP_MAX_AGE_SECONDS", 26 * 60 * 60),
        queue_warn_seconds=_env_int("OPS_QUEUE_WARN_SECONDS", 5 * 60),
        queue_critical_seconds=_env_int("OPS_QUEUE_CRITICAL_SECONDS", 30 * 60),
        stale_processing_seconds=_env_int("OPS_STALE_PROCESSING_SECONDS", 60),
        dead_jobs_warn_24h=_env_int("OPS_DEAD_JOBS_WARN_24H", 1),
        dead_jobs_critical_24h=_env_int("OPS_DEAD_JOBS_CRITICAL_24H", 5),
        generation_p95_warn_ms=_env_int("OPS_GENERATION_P95_WARN_MS", 60_000),
        generation_p95_critical_ms=_env_int("OPS_GENERATION_P95_CRITICAL_MS", 120_000),
    )
    if thresholds.queue_critical_seconds <= thresholds.queue_warn_seconds:
        raise RuntimeError("OPS_QUEUE_CRITICAL_SECONDS must exceed OPS_QUEUE_WARN_SECONDS")
    if thresholds.dead_jobs_critical_24h <= thresholds.dead_jobs_warn_24h:
        raise RuntimeError("OPS_DEAD_JOBS_CRITICAL_24H must exceed OPS_DEAD_JOBS_WARN_24H")
    if thresholds.generation_p95_critical_ms <= thresholds.generation_p95_warn_ms:
        raise RuntimeError(
            "OPS_GENERATION_P95_CRITICAL_MS must exceed OPS_GENERATION_P95_WARN_MS"
        )
    return thresholds


def assess_operational_health(
    metrics: dict[str, Any], thresholds: OperationalThresholds
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []

    def add(severity: str, signal: str, value: Any, threshold: Any) -> None:
        alerts.append(
            {
                "severity": severity,
                "signal": signal,
                "value": value,
                "threshold": threshold,
            }
        )

    backup_age = metrics["backup"]["age_seconds"]
    if backup_age is None:
        add("critical", "backup_missing", None, thresholds.backup_max_age_seconds)
    elif backup_age > thresholds.backup_max_age_seconds:
        add(
            "critical",
            "backup_age_seconds",
            backup_age,
            thresholds.backup_max_age_seconds,
        )

    queue_age = metrics["queue"]["oldest_runnable_pending_seconds"]
    if queue_age >= thresholds.queue_critical_seconds:
        add("critical", "queue_lag_seconds", queue_age, thresholds.queue_critical_seconds)
    elif queue_age >= thresholds.queue_warn_seconds:
        add("degraded", "queue_lag_seconds", queue_age, thresholds.queue_warn_seconds)

    stale = metrics["queue"]["stale_processing"]
    if stale > 0:
        add("critical", "stale_processing_jobs", stale, 0)

    dead_24h = metrics["queue"]["dead_last_24h"]
    if dead_24h >= thresholds.dead_jobs_critical_24h:
        add("critical", "dead_jobs_24h", dead_24h, thresholds.dead_jobs_critical_24h)
    elif dead_24h >= thresholds.dead_jobs_warn_24h:
        add("degraded", "dead_jobs_24h", dead_24h, thresholds.dead_jobs_warn_24h)

    p95_ms = metrics["summaries"]["p95_generation_ms"]
    if p95_ms >= thresholds.generation_p95_critical_ms:
        add(
            "critical",
            "summary_generation_p95_ms",
            p95_ms,
            thresholds.generation_p95_critical_ms,
        )
    elif p95_ms >= thresholds.generation_p95_warn_ms:
        add(
            "degraded",
            "summary_generation_p95_ms",
            p95_ms,
            thresholds.generation_p95_warn_ms,
        )

    status = "ok"
    if any(alert["severity"] == "critical" for alert in alerts):
        status = "critical"
    elif alerts:
        status = "degraded"
    return {"status": status, "alerts": alerts}


async def collect_operational_metrics(conn: asyncpg.Connection) -> dict[str, Any]:
    thresholds = operational_thresholds()
    queue_rows = await conn.fetch(
        """
        SELECT status::text AS status, count(*)::bigint AS count
        FROM jobs
        GROUP BY status
        """
    )
    queue_counts = {row["status"]: int(row["count"]) for row in queue_rows}
    for status in ("pending", "processing", "completed", "dead"):
        queue_counts.setdefault(status, 0)

    queue_metrics = await conn.fetchrow(
        """
        SELECT
            COALESCE(
                EXTRACT(
                    EPOCH FROM (
                        NOW() - min(run_at) FILTER (
                            WHERE status = 'pending' AND run_at <= NOW()
                        )
                    )
                ),
                0
            ) AS oldest_runnable_pending_seconds,
            count(*) FILTER (
                WHERE status = 'processing'
                  AND (last_heartbeat IS NULL OR last_heartbeat < NOW() - make_interval(secs => $1))
            )::bigint AS stale_processing,
            count(*) FILTER (
                WHERE status = 'dead' AND updated_at >= NOW() - interval '24 hours'
            )::bigint AS dead_last_24h
        FROM jobs
        """,
        thresholds.stale_processing_seconds,
    )
    dead_by_task_rows = await conn.fetch(
        """
        SELECT task_name, count(*)::bigint AS count
        FROM jobs
        WHERE status = 'dead' AND updated_at >= NOW() - interval '24 hours'
        GROUP BY task_name
        ORDER BY count DESC, task_name
        """
    )

    summary_metrics = await conn.fetchrow(
        """
        SELECT
            count(*)::bigint AS total,
            count(*) FILTER (WHERE validation->>'passed' = 'false')::bigint AS validation_failed,
            COALESCE(avg(generation_ms) FILTER (WHERE generation_ms IS NOT NULL), 0) AS avg_generation_ms,
            COALESCE(
                percentile_cont(0.95) WITHIN GROUP (ORDER BY generation_ms)
                    FILTER (WHERE generation_ms IS NOT NULL),
                0
            ) AS p95_generation_ms
        FROM process_summaries
        """
    )
    webhook_metrics = await conn.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE received_at >= NOW() - interval '1 hour')::bigint AS received_last_hour,
            EXTRACT(EPOCH FROM (NOW() - max(received_at))) AS seconds_since_last
        FROM judit_deliveries
        """
    )
    backup_metrics = await conn.fetchrow(
        """
        SELECT
            EXTRACT(EPOCH FROM (NOW() - max(completed_at))) AS age_seconds,
            max(completed_at) AS last_completed_at
        FROM backup_runs
        """
    )

    total = int(summary_metrics["total"] or 0)
    failed = int(summary_metrics["validation_failed"] or 0)
    metrics: dict[str, Any] = {
        "queue": {
            "counts": queue_counts,
            "oldest_runnable_pending_seconds": float(
                queue_metrics["oldest_runnable_pending_seconds"] or 0
            ),
            "stale_processing": int(queue_metrics["stale_processing"] or 0),
            "dead_last_24h": int(queue_metrics["dead_last_24h"] or 0),
            "dead_by_task_last_24h": {
                row["task_name"]: int(row["count"]) for row in dead_by_task_rows
            },
        },
        "summaries": {
            "total": total,
            "validation_failed": failed,
            "validation_failure_rate": (failed / total) if total else 0.0,
            "avg_generation_ms": float(summary_metrics["avg_generation_ms"] or 0),
            "p95_generation_ms": float(summary_metrics["p95_generation_ms"] or 0),
        },
        "webhooks": {
            "received_last_hour": int(webhook_metrics["received_last_hour"] or 0),
            "seconds_since_last": (
                float(webhook_metrics["seconds_since_last"])
                if webhook_metrics["seconds_since_last"] is not None
                else None
            ),
        },
        "backup": {
            "age_seconds": (
                float(backup_metrics["age_seconds"])
                if backup_metrics["age_seconds"] is not None
                else None
            ),
            "last_completed_at": backup_metrics["last_completed_at"],
        },
    }
    metrics["operational_health"] = assess_operational_health(metrics, thresholds)
    return metrics


async def list_failed_summaries(conn: asyncpg.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent process summaries that failed post-generation validation.

    This is the explicit exposure surface for ``validation.passed: false`` required
    by the product contract; operators can act on these rows instead of parsing raw
    metrics counters.
    """
    rows = await conn.fetch(
        """
        SELECT p.code,
               p.court,
               p.class_name,
               ps.model,
               ps.prompt_version,
               ps.validation,
               ps.generation_ms,
               ps.created_at
        FROM process_summaries ps
        JOIN processes p ON p.id = ps.process_id
        WHERE ps.validation->>'passed' = 'false'
        ORDER BY ps.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    failed: list[dict[str, Any]] = []
    for row in rows:
        validation = row["validation"]
        if isinstance(validation, str):
            try:
                validation = json.loads(validation)
            except (TypeError, ValueError):
                validation = {}
        failed.append(
            {
                "code": row["code"],
                "court": row["court"],
                "class_name": row["class_name"],
                "model": row["model"],
                "prompt_version": row["prompt_version"],
                "validation": validation if isinstance(validation, dict) else {},
                "generation_ms": row["generation_ms"],
                "created_at": (
                    row["created_at"].isoformat() if row["created_at"] is not None else None
                ),
            }
        )
    return failed
