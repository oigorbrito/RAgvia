from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import signal
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

import app.judit_tasks  # noqa: F401 - imports task registrations
import app.judit_attachments  # noqa: F401 - imports task registrations
import app.process_requests  # noqa: F401 - imports task registrations
import app.judit_tracking  # noqa: F401 - imports task registrations
import app.datajud_tasks  # noqa: F401 - imports task registrations
import app.rag  # noqa: F401 - imports task registrations
from app.db import create_pool
from app.datajud_security import (
    DataJudProcessKnownSecret,
    DataJudProcessNotAuthorized,
    DataJudProcessNotPublic,
    classify_datajud_lookup,
)
from app.json_utils import decode_json_object
from app.judit import normalize_cnj
from app.langfuse_tracing import SummaryTrace, start_summary_trace
from app.log_safety import sanitize_error_message
from app.public_lifecycle_worker import (
    mark_job_dead,
    mark_job_started,
    reconcile_generation_result,
)
from app.queue import claim, complete, fail, heartbeat, reclaim_stale
from app.tasks import PermanentTaskError, resolve_task

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _transaction(conn):
    transaction = getattr(conn, "transaction", None)
    if transaction is None:
        yield
        return
    async with transaction():
        yield


@dataclass(slots=True)
class WorkerSettings:
    database_url: str
    concurrency: int = 2
    poll_interval_seconds: float = 1.0
    heartbeat_interval_seconds: float = 10.0
    stale_after_seconds: int = 45
    task_timeout_seconds: float = 120.0
    reclaim_interval_seconds: float = 15.0
    shutdown_grace_seconds: float = 30.0

    def validate(self) -> "WorkerSettings":
        positive = {
            "concurrency": self.concurrency,
            "poll_interval_seconds": self.poll_interval_seconds,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "task_timeout_seconds": self.task_timeout_seconds,
            "reclaim_interval_seconds": self.reclaim_interval_seconds,
            "shutdown_grace_seconds": self.shutdown_grace_seconds,
        }
        for name, value in positive.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.stale_after_seconds <= self.heartbeat_interval_seconds:
            raise ValueError(
                "stale_after_seconds must be greater than heartbeat_interval_seconds"
            )
        return self

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        return cls(
            database_url=database_url,
            concurrency=int(os.getenv("WORKER_CONCURRENCY", "2")),
            poll_interval_seconds=float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1")),
            heartbeat_interval_seconds=float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "10")),
            stale_after_seconds=int(os.getenv("WORKER_STALE_AFTER_SECONDS", "45")),
            task_timeout_seconds=float(os.getenv("WORKER_TASK_TIMEOUT_SECONDS", "120")),
            reclaim_interval_seconds=float(os.getenv("WORKER_RECLAIM_INTERVAL_SECONDS", "15")),
            shutdown_grace_seconds=float(os.getenv("WORKER_SHUTDOWN_GRACE_SECONDS", "30")),
        ).validate()


def _decode_payload(value: Any) -> dict[str, Any]:
    return decode_json_object(value, label="job payload")


def _resolve_job_contract(row: asyncpg.Record) -> tuple[Any, dict[str, Any]]:
    try:
        handler = resolve_task(str(row["task_name"]))
        payload = _decode_payload(row["payload"])
    except (KeyError, LookupError, TypeError, ValueError) as exc:
        detail = sanitize_error_message(exc)
        raise PermanentTaskError(
            f"invalid job contract ({type(exc).__name__}): {detail}"
        ) from None
    return handler, payload


async def _summary_trace_details(
    conn: asyncpg.Connection, payload: dict[str, Any]
) -> tuple[str | None, list[str]]:
    try:
        process_id = UUID(str(payload["process_id"]))
        version_id = UUID(str(payload["version_id"]))
    except (KeyError, TypeError, ValueError):
        return None, []

    rows = await conn.fetch(
        """
        SELECT ps.id AS summary_id, pss.step_id
        FROM process_summaries ps
        LEFT JOIN process_summary_sources pss
          ON pss.summary_id = ps.id
        WHERE ps.process_id = $1 AND ps.version_id = $2
        ORDER BY pss.source_order NULLS LAST
        """,
        process_id,
        version_id,
    )
    if not rows:
        return None, []
    summary_id = str(rows[0]["summary_id"])
    source_ids = [str(row["step_id"]) for row in rows if row["step_id"] is not None]
    return summary_id, source_ids


class Worker:
    def __init__(self, pool: asyncpg.Pool, settings: WorkerSettings, worker_id: UUID | None = None):
        self.pool = pool
        self.settings = settings.validate()
        self.worker_id = worker_id or uuid4()
        self.stop_event = asyncio.Event()

    async def _verified_datajud_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_tenant_id = payload.get("tenant_id")
        raw_code = payload.get("code")
        try:
            tenant_id = UUID(str(raw_tenant_id))
            if not isinstance(raw_code, str):
                raise ValueError("invalid process code")
            code = normalize_cnj(raw_code)
        except (TypeError, ValueError):
            raise PermanentTaskError("DataJud job requires an authorized public process") from None

        if payload.get("operation") != "metadata_lookup" or payload.get("purpose") != "public_metadata_lookup":
            raise PermanentTaskError("DataJud job purpose is invalid")
        try:
            async with self.pool.acquire() as conn:
                classification, secrecy_level = await classify_datajud_lookup(
                    conn, tenant_id=tenant_id, code=code
                )
        except DataJudProcessKnownSecret:
            raise PermanentTaskError("DataJud job is blocked for a known restricted process") from None
        except (DataJudProcessNotAuthorized, DataJudProcessNotPublic):
            raise PermanentTaskError("DataJud job authorization is invalid") from None

        trusted_payload = dict(payload)
        trusted_payload["code"] = code
        trusted_payload["classification_at_execution"] = classification
        trusted_payload["secrecy_level"] = secrecy_level
        return trusted_payload

    async def _heartbeat_loop(self, job_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            async with self.pool.acquire() as conn:
                alive = await heartbeat(conn, job_id, self.worker_id)
            if not alive:
                return

    async def _run_job(self, row: asyncpg.Record) -> None:
        job_id = row["id"]
        task_name = str(row["task_name"])
        payload: dict[str, Any] = {}
        heartbeat_task: asyncio.Task[None] | None = None
        summary_trace = SummaryTrace()
        try:
            handler, payload = _resolve_job_contract(row)
            if task_name == "lookup_datajud_metadata":
                payload = await self._verified_datajud_payload(payload)
            if task_name == "generate_process_summary":
                async with self.pool.acquire() as conn:
                    async with _transaction(conn):
                        owns_job = await heartbeat(conn, job_id, self.worker_id)
                        if not owns_job:
                            logger.warning(
                                "job %s lost ownership before lifecycle start",
                                job_id,
                            )
                            return
                        await mark_job_started(
                            conn,
                            task_name=task_name,
                            payload=payload,
                        )
                summary_trace = start_summary_trace(
                    job_id=job_id,
                    process_id=payload.get("process_id"),
                    version_id=payload.get("version_id"),
                    judit_request_id=payload.get("judit_request_id"),
                )
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))
            result = await asyncio.wait_for(
                handler(payload), timeout=self.settings.task_timeout_seconds
            )
            trace_result = dict(result or {})
            trace_source_ids: list[str] = []
            async with self.pool.acquire() as conn:
                async with _transaction(conn):
                    completed = await complete(
                        conn,
                        job_id,
                        self.worker_id,
                        result or {},
                    )
                    if not completed:
                        logger.warning(
                            "job %s lost ownership before completion",
                            job_id,
                        )
                        if task_name == "generate_process_summary":
                            summary_trace.finish(error_type="LostJobOwnership")
                        return
                    if task_name == "generate_process_summary":
                        await reconcile_generation_result(
                            conn,
                            payload=payload,
                            result=result,
                        )
                        summary_id, trace_source_ids = await _summary_trace_details(conn, payload)
                        if summary_id is not None:
                            trace_result["summary_id"] = summary_id
            if task_name == "generate_process_summary":
                validation = trace_result.get("validation")
                validation_errors = (
                    validation.get("errors")
                    if isinstance(validation, dict) and isinstance(validation.get("errors"), list)
                    else None
                )
                summary_trace.finish(
                    result=trace_result,
                    source_ids=trace_source_ids,
                    validation_errors=validation_errors,
                )
        except asyncio.CancelledError:
            summary_trace.finish(error_type="CancelledError")
            raise
        except Exception as exc:
            summary_trace.finish(error_type=type(exc).__name__)
            permanent = isinstance(exc, PermanentTaskError)
            safe_detail = sanitize_error_message(exc)
            logger.error(
                "job %s failed%s (%s): %s",
                job_id,
                " permanently" if permanent else "",
                type(exc).__name__,
                safe_detail,
            )
            async with self.pool.acquire() as conn:
                async with _transaction(conn):
                    next_status = await fail(
                        conn,
                        job_id,
                        self.worker_id,
                        attempts=int(row["attempts"]),
                        error=f"{type(exc).__name__}: {safe_detail}",
                        permanent=permanent,
                    )
                    if next_status == "dead":
                        await mark_job_dead(
                            conn,
                            task_name=task_name,
                            payload=payload,
                            error_code="job_failed",
                        )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

    async def process_one(self) -> bool:
        if self.stop_event.is_set():
            return False
        async with self.pool.acquire() as conn:
            row = await claim(conn, self.worker_id)
        if row is None:
            return False
        await self._run_job(row)
        return True

    async def _slot_loop(self, slot: int) -> None:
        logger.info("worker %s slot %s started", self.worker_id, slot)
        while not self.stop_event.is_set():
            processed = await self.process_one()
            if not processed:
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(), timeout=self.settings.poll_interval_seconds
                    )
                except TimeoutError:
                    pass

    async def _reclaimer_loop(self) -> None:
        while not self.stop_event.is_set():
            async with self.pool.acquire() as conn:
                async with _transaction(conn):
                    reclaimed = await reclaim_stale(conn, self.settings.stale_after_seconds)
                    for row in reclaimed:
                        if str(row["status"]) != "dead":
                            continue
                        try:
                            payload = _decode_payload(row["payload"])
                        except (TypeError, ValueError):
                            payload = {}
                        await mark_job_dead(
                            conn,
                            task_name=str(row["task_name"]),
                            payload=payload,
                            error_code="job_reclaim_exhausted",
                        )
            if reclaimed:
                logger.warning("reclaimed %s stale jobs", len(reclaimed))
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.reclaim_interval_seconds
                )
            except TimeoutError:
                pass

    async def run(self) -> None:
        slot_tasks = [asyncio.create_task(self._slot_loop(slot)) for slot in range(self.settings.concurrency)]
        reclaimer_task = asyncio.create_task(self._reclaimer_loop())
        await self.stop_event.wait()
        logger.info("worker %s draining active jobs for up to %.1fs", self.worker_id, self.settings.shutdown_grace_seconds)
        reclaimer_task.cancel()
        with suppress(asyncio.CancelledError):
            await reclaimer_task
        done, pending = await asyncio.wait(slot_tasks, timeout=self.settings.shutdown_grace_seconds)
        if pending:
            logger.warning("worker %s shutdown grace expired; cancelling %s active slot(s)", self.worker_id, len(pending))
            for item in pending:
                item.cancel()
        await asyncio.gather(*slot_tasks, return_exceptions=True)
        logger.info("worker %s shutdown complete (%s slot(s) drained)", self.worker_id, len(done))

    def stop(self) -> None:
        self.stop_event.set()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Rpy PostgreSQL worker")
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args()
    settings = WorkerSettings.from_env()
    app.rag.provider_context_limits()
    if args.concurrency is not None:
        settings.concurrency = args.concurrency
        settings.validate()
    pool = await create_pool(settings.database_url, min_size=1, max_size=max(4, settings.concurrency + 2))
    worker = Worker(pool, settings)
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, worker.stop)
    try:
        await worker.run()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
