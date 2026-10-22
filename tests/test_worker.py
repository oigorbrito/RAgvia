import asyncio
import logging
from uuid import uuid4

import pytest

import app.worker as worker_module
from app.tasks import PermanentTaskError
from app.worker import Worker, WorkerSettings, _decode_payload


def test_worker_settings_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/rpy")
    settings = WorkerSettings.from_env()
    assert settings.concurrency == 2
    assert settings.stale_after_seconds > settings.heartbeat_interval_seconds
    assert settings.task_timeout_seconds > 0


@pytest.mark.parametrize(
    "field",
    [
        "poll_interval_seconds",
        "heartbeat_interval_seconds",
        "task_timeout_seconds",
        "reclaim_interval_seconds",
        "shutdown_grace_seconds",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_worker_settings_reject_non_finite_timing_controls(
    field: str,
    value: float,
) -> None:
    settings = WorkerSettings(
        database_url="postgresql://localhost/rpy",
        heartbeat_interval_seconds=1.0,
        stale_after_seconds=2,
    )
    setattr(settings, field, value)
    with pytest.raises(ValueError, match="must be finite"):
        settings.validate()


def test_decode_payload_accepts_mapping_and_json_string() -> None:
    assert _decode_payload({"request_id": "req-1"}) == {"request_id": "req-1"}
    assert _decode_payload('{"request_id":"req-2"}') == {"request_id": "req-2"}
    assert _decode_payload(None) == {}


def test_decode_payload_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="object"):
        _decode_payload('["not", "an", "object"]')


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


def _settings(*, task_timeout_seconds: float = 1) -> WorkerSettings:
    return WorkerSettings(
        database_url="postgresql://unused/rpy",
        concurrency=1,
        heartbeat_interval_seconds=1,
        stale_after_seconds=2,
        task_timeout_seconds=task_timeout_seconds,
        reclaim_interval_seconds=1,
    )


@pytest.mark.asyncio
async def test_worker_task_timeout_remains_retryable(monkeypatch) -> None:
    failed: list[tuple[str, bool]] = []

    async def hung_handler(payload):
        await asyncio.sleep(60)

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append((error, permanent))
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: hung_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)

    worker = Worker(_Pool(), _settings(task_timeout_seconds=0.01), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "hung-provider-call",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert len(failed) == 1
    assert failed[0][0].startswith("TimeoutError:")
    assert failed[0][1] is False


@pytest.mark.asyncio
async def test_worker_marks_explicit_permanent_task_error(monkeypatch) -> None:
    failed: list[tuple[str, bool]] = []

    async def permanent_handler(payload):
        raise PermanentTaskError("deterministic contract failure")

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append((error, permanent))
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: permanent_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "permanent",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert failed == [("PermanentTaskError: deterministic contract failure", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_detail", "extra_secrets"),
    [
        ("", ()),
        (
            " callback_key='SYNTHETIC_CALLBACK_252 with spaces'"
            " pwd=[REDACTED]SYNTHETIC_TAIL_252 rpy_SYNTHETIC_TOKEN_252"
            " passcode=SYNTHETIC_PASSCODE_273",
            ("SYNTHETIC_CALLBACK_252", "SYNTHETIC_TAIL_252", "rpy_SYNTHETIC_TOKEN_252", "SYNTHETIC_PASSCODE_273"),
        ),
    ],
)
async def test_worker_redacts_secret_from_error_log_and_runtime_log(
    monkeypatch, caplog, extra_detail: str, extra_secrets: tuple[str, ...]
) -> None:
    failed: list[str] = []
    failure_calls: list[dict] = []
    secret = "sk-provider-secret-value"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    async def leaking_handler(payload):
        raise RuntimeError(
            f"provider failed with {secret} at "
            "postgresql://rpy_worker:db-password@postgres:5432/rpy"
            f"{extra_detail} request_id=diagnostic-252 status=502"
        )

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append(error)
        failure_calls.append({
            "job_id": job_id, "worker_id": worker_id,
            "attempts": attempts, "permanent": permanent,
        })
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: leaking_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)
    caplog.set_level(logging.ERROR, logger="app.worker")

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "leaking-provider",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert len(failed) == 1
    assert secret not in failed[0]
    assert "db-password" not in failed[0]
    assert "[REDACTED]" in failed[0]
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in rendered_logs
    assert "db-password" not in rendered_logs
    assert "[REDACTED]" in rendered_logs
    assert all(record.exc_info is None for record in caplog.records)
    for rendered in (failed[0], rendered_logs):
        for synthetic_secret in extra_secrets:
            if synthetic_secret in rendered:
                raise AssertionError("Worker output retained a synthetic credential")
        if "request_id=diagnostic-252" not in rendered or "status=502" not in rendered:
            raise AssertionError("Worker output lost public diagnostic context")
    if failure_calls != [{
        "job_id": row["id"], "worker_id": worker.worker_id,
        "attempts": row["attempts"], "permanent": False,
    }]:
        raise AssertionError("Redaction changed queue-failure identity or retry classification")


class _Trace:
    def __init__(self) -> None:
        self.finished: list[dict] = []

    def finish(self, **kwargs) -> None:
        self.finished.append(kwargs)


@pytest.mark.asyncio
async def test_summary_worker_lost_ownership_before_start_has_no_lifecycle_side_effect(
    monkeypatch,
) -> None:
    called: list[str] = []

    async def handler(payload):
        called.append("handler")
        return {"validation": {"passed": True}}

    async def not_owner(conn, job_id, worker_id):
        return False

    async def mark_started(conn, *, task_name, payload):
        called.append("mark_started")

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: handler)
    monkeypatch.setattr(worker_module, "heartbeat", not_owner)
    monkeypatch.setattr(worker_module, "mark_job_started", mark_started)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "generate_process_summary",
        "payload": {"process_id": str(uuid4()), "version_id": str(uuid4())},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert called == []  # nosec B101


@pytest.mark.asyncio
async def test_summary_worker_lost_ownership_before_completion_skips_reconcile(
    monkeypatch,
) -> None:
    called: list[str] = []
    trace = _Trace()

    async def handler(payload):
        called.append("handler")
        return {"validation": {"passed": True}}

    async def owns_job(conn, job_id, worker_id):
        return True

    async def mark_started(conn, *, task_name, payload):
        called.append("mark_started")

    async def lost_complete(conn, job_id, worker_id, result):
        called.append("complete")
        return False

    async def reconcile(conn, *, payload, result):
        called.append("reconcile")

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: handler)
    monkeypatch.setattr(worker_module, "heartbeat", owns_job)
    monkeypatch.setattr(worker_module, "mark_job_started", mark_started)
    monkeypatch.setattr(worker_module, "complete", lost_complete)
    monkeypatch.setattr(worker_module, "reconcile_generation_result", reconcile)
    monkeypatch.setattr(worker_module, "start_summary_trace", lambda **kwargs: trace)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "generate_process_summary",
        "payload": {"process_id": str(uuid4()), "version_id": str(uuid4())},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert called == ["mark_started", "handler", "complete"]  # nosec B101
    assert trace.finished == [{"error_type": "LostJobOwnership"}]  # nosec B101


@pytest.mark.asyncio
async def test_summary_worker_completes_fence_before_reconcile(monkeypatch) -> None:
    called: list[str] = []
    trace = _Trace()

    async def handler(payload):
        return {"validation": {"passed": True}}

    async def owns_job(conn, job_id, worker_id):
        return True

    async def mark_started(conn, *, task_name, payload):
        called.append("mark_started")

    async def complete_owned(conn, job_id, worker_id, result):
        called.append("complete")
        return True

    async def reconcile(conn, *, payload, result):
        called.append("reconcile")

    async def trace_details(conn, payload):
        return None, []

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: handler)
    monkeypatch.setattr(worker_module, "heartbeat", owns_job)
    monkeypatch.setattr(worker_module, "mark_job_started", mark_started)
    monkeypatch.setattr(worker_module, "complete", complete_owned)
    monkeypatch.setattr(worker_module, "reconcile_generation_result", reconcile)
    monkeypatch.setattr(worker_module, "_summary_trace_details", trace_details)
    monkeypatch.setattr(worker_module, "start_summary_trace", lambda **kwargs: trace)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "generate_process_summary",
        "payload": {"process_id": str(uuid4()), "version_id": str(uuid4())},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert called == ["mark_started", "complete", "reconcile"]  # nosec B101
    assert trace.finished and trace.finished[0].get("error_type") is None  # nosec B101


@pytest.mark.asyncio
async def test_worker_redacts_nested_authorization_from_log_and_queue_failure(
    monkeypatch, caplog
) -> None:
    sentinel = "SYNTH_WORKER_NESTED_AUTH_281"
    failed: list[str] = []

    async def leaking_handler(payload):
        raise RuntimeError(
            '{"error":{"headers":{"authorization":"OAuth '
            f"{sentinel}"
            '"}},"status":503,"request_id":"nested-281"}'
        )

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append(error)
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: leaking_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)
    caplog.set_level(logging.ERROR, logger="app.worker")

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {"id": uuid4(), "task_name": "nested-auth-error", "payload": {}, "attempts": 1}
    await worker._run_job(row)

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    if len(failed) != 1:
        raise AssertionError("worker did not persist exactly one failure detail")
    for rendered in (failed[0], rendered_logs):
        if sentinel in rendered:
            raise AssertionError("nested Authorization credential reached an error sink")
        if (
            "[REDACTED]" not in rendered
            or '"status":503' not in rendered
            or "nested-281" not in rendered
        ):
            raise AssertionError("redaction lost diagnostics or failed to mask the credential")
