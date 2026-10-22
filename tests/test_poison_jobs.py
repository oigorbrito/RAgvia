from uuid import uuid4

import pytest

import app.worker as worker_module
from app.worker import Worker, WorkerSettings


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


def _settings() -> WorkerSettings:
    return WorkerSettings(
        database_url="postgresql://unused/rpy",
        concurrency=1,
        heartbeat_interval_seconds=1,
        stale_after_seconds=2,
        task_timeout_seconds=1,
        reclaim_interval_seconds=1,
        shutdown_grace_seconds=1,
    )


@pytest.mark.asyncio
async def test_malformed_payload_dead_letters_without_starting_heartbeat(monkeypatch) -> None:
    failed: list[tuple[str, bool]] = []
    heartbeat_started = False

    async def handler(payload):
        raise AssertionError("handler must not run for malformed payload")

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append((error, permanent))
        return "dead"

    async def fake_heartbeat(job_id):
        nonlocal heartbeat_started
        heartbeat_started = True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    monkeypatch.setattr(worker, "_heartbeat_loop", fake_heartbeat)

    await worker._run_job(
        {
            "id": uuid4(),
            "task_name": "known-task",
            "payload": '["not", "an", "object"]',
            "attempts": 1,
        }
    )

    assert heartbeat_started is False
    assert len(failed) == 1
    assert failed[0][1] is True
    assert "PermanentTaskError" in failed[0][0]
    assert "invalid job contract" in failed[0][0]


@pytest.mark.asyncio
async def test_unknown_task_dead_letters_without_escaping_slot(monkeypatch) -> None:
    failed: list[tuple[str, bool]] = []

    def unknown_task(name):
        raise LookupError(f"unknown task: {name}")

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append((error, permanent))
        return "dead"

    monkeypatch.setattr(worker_module, "resolve_task", unknown_task)
    monkeypatch.setattr(worker_module, "fail", fake_fail)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    await worker._run_job(
        {
            "id": uuid4(),
            "task_name": "does-not-exist",
            "payload": {},
            "attempts": 1,
        }
    )

    assert len(failed) == 1
    assert failed[0][1] is True
    assert "unknown task" in failed[0][0]
