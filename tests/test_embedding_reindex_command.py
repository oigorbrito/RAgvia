from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reindex_embedding_space.py"
_SPEC = importlib.util.spec_from_file_location("reindex_embedding_space", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_REINDEX = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_REINDEX)


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


class FakePool:
    def __init__(self, rows):
        self.conn = FakeConnection(rows)

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeRuntime:
    def __init__(self, provider="bge", model="BAAI/bge-m3"):
        self.space = SimpleNamespace(
            provider=provider,
            model=model,
            key=f"{provider}:{model}:1024",
        )
        self.calls = []

    async def ensure_step_embeddings(self, pool, *, version_id):
        self.calls.append(version_id)
        return 3


@pytest.mark.asyncio
async def test_reindex_batch_is_resumable_and_skips_complete_versions() -> None:
    first = uuid4()
    second = uuid4()
    pool = FakePool(
        [
            {"id": first, "created_at": object(), "missing_embeddings": 0},
            {"id": second, "created_at": object(), "missing_embeddings": 4},
        ]
    )
    runtime = FakeRuntime()

    report = await _REINDEX.reindex_batch(pool, runtime=runtime, limit=2)

    assert runtime.calls == [second]
    assert report == {
        "space": "bge:BAAI/bge-m3:1024",
        "dry_run": False,
        "versions_seen": 2,
        "versions_with_missing": 1,
        "embeddings_written": 3,
        "next_after_version_id": str(second),
        "has_more": True,
    }
    _, args = pool.conn.calls[0]
    assert args[:3] == ("bge", "BAAI/bge-m3", 2)
    assert args[3] is None


@pytest.mark.asyncio
async def test_external_reindex_query_excludes_secret_processes() -> None:
    pool = FakePool([])
    runtime = FakeRuntime(provider="cohere", model="embed-v4.0")

    report = await _REINDEX.reindex_batch(pool, runtime=runtime, limit=5, dry_run=True)

    query, args = pool.conn.calls[0]
    assert "($1 <> 'cohere' OR coalesce(p.secrecy_level, 0) = 0)" in query
    assert "JOIN processes p ON p.id = pv.process_id" in query
    assert args[:3] == ("cohere", "embed-v4.0", 5)
    assert report["space"] == "cohere:embed-v4.0:1024"


@pytest.mark.asyncio
async def test_dry_run_reports_missing_without_loading_vectors() -> None:
    version_id = uuid4()
    pool = FakePool(
        [{"id": version_id, "created_at": object(), "missing_embeddings": 9}]
    )
    runtime = FakeRuntime()

    report = await _REINDEX.reindex_batch(
        pool,
        runtime=runtime,
        limit=10,
        after_version_id=uuid4(),
        dry_run=True,
    )

    assert runtime.calls == []
    assert report["versions_with_missing"] == 1
    assert report["embeddings_written"] == 0
    assert report["has_more"] is False


@pytest.mark.asyncio
async def test_reindex_rejects_nonpositive_batch_size() -> None:
    pool = FakePool([])
    runtime = FakeRuntime()

    with pytest.raises(ValueError, match="version batch limit must be positive"):
        await _REINDEX.reindex_batch(pool, runtime=runtime, limit=0)


@pytest.mark.asyncio
async def test_reindex_all_accumulates_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    first = uuid4()
    second = uuid4()
    reports = iter(
        [
            {
                "space": "bge:BAAI/bge-m3:1024",
                "dry_run": False,
                "versions_seen": 2,
                "versions_with_missing": 1,
                "embeddings_written": 3,
                "next_after_version_id": str(first),
                "has_more": True,
            },
            {
                "space": "bge:BAAI/bge-m3:1024",
                "dry_run": False,
                "versions_seen": 1,
                "versions_with_missing": 1,
                "embeddings_written": 2,
                "next_after_version_id": str(second),
                "has_more": False,
            },
        ]
    )
    checkpoints = []

    async def fake_batch(pool, *, runtime, limit, after_version_id, dry_run):
        checkpoints.append(after_version_id)
        return next(reports)

    monkeypatch.setattr(_REINDEX, "reindex_batch", fake_batch)
    runtime = FakeRuntime()
    report = await _REINDEX.reindex_all(object(), runtime=runtime, limit=2)

    assert checkpoints == [None, first]
    assert report["batches"] == 2
    assert report["versions_seen"] == 3
    assert report["versions_with_missing"] == 2
    assert report["embeddings_written"] == 5
    assert report["completed"] is True
    assert report["last_version_id"] == str(second)


@pytest.mark.asyncio
async def test_reindex_all_rejects_repeated_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = uuid4()
    calls = 0

    async def fake_batch(pool, *, runtime, limit, after_version_id, dry_run):
        nonlocal calls
        calls += 1
        return {
            "space": runtime.space.key,
            "dry_run": dry_run,
            "versions_seen": 1,
            "versions_with_missing": 1,
            "embeddings_written": 0,
            "next_after_version_id": str(checkpoint),
            "has_more": True,
        }

    monkeypatch.setattr(_REINDEX, "reindex_batch", fake_batch)
    runtime = FakeRuntime()

    with pytest.raises(RuntimeError, match="checkpoint repeated"):
        await _REINDEX.reindex_all(object(), runtime=runtime, limit=1, dry_run=True)
    assert calls == 2
