import asyncio
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


def _settings(*, grace: float = 0.2, heartbeat: float = 0.01) -> WorkerSettings:
    return WorkerSettings(
        database_url="postgresql://unused/rpy",
        concurrency=1,
        poll_interval_seconds=0.01,
        heartbeat_interval_seconds=heartbeat,
        stale_after_seconds=1,
        task_timeout_seconds=5,
        reclaim_interval_seconds=0.01,
        shutdown_grace_seconds=grace,
    )


@pytest.mark.asyncio
async def test_job_heartbeat_continues_after_process_stop_signal(monkeypatch) -> None:
    calls = 0

    async def fake_heartbeat(conn, job_id, worker_id):
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(worker_module, "heartbeat", fake_heartbeat)
    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    worker.stop()

    await worker._heartbeat_loop(uuid4())

    assert calls == 1


@pytest.mark.asyncio
async def test_run_waits_for_active_slot_to_finish_within_grace(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_slot_loop(slot):
        started.set()
        await release.wait()

    async def fake_reclaimer_loop():
        await asyncio.Event().wait()

    worker = Worker(_Pool(), _settings(grace=0.5), worker_id=uuid4())
    monkeypatch.setattr(worker, "_slot_loop", fake_slot_loop)
    monkeypatch.setattr(worker, "_reclaimer_loop", fake_reclaimer_loop)

    run_task = asyncio.create_task(worker.run())
    await started.wait()
    worker.stop()
    await asyncio.sleep(0.02)

    assert run_task.done() is False

    release.set()
    await asyncio.wait_for(run_task, timeout=0.2)


@pytest.mark.asyncio
async def test_run_cancels_active_slot_after_grace_expires(monkeypatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_slot_loop(slot):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_reclaimer_loop():
        await asyncio.Event().wait()

    worker = Worker(_Pool(), _settings(grace=0.02), worker_id=uuid4())
    monkeypatch.setattr(worker, "_slot_loop", fake_slot_loop)
    monkeypatch.setattr(worker, "_reclaimer_loop", fake_reclaimer_loop)

    run_task = asyncio.create_task(worker.run())
    await started.wait()
    worker.stop()
    await asyncio.wait_for(run_task, timeout=0.2)

    assert cancelled.is_set()
