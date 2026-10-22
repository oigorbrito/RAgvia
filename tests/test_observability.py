from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.observability import (
    OperationalThresholds,
    assess_operational_health,
    list_failed_summaries,
    operational_thresholds,
)


def _metrics() -> dict:
    return {
        "backup": {"age_seconds": 10.0},
        "queue": {
            "oldest_runnable_pending_seconds": 0.0,
            "stale_processing": 0,
            "dead_last_24h": 0,
        },
        "summaries": {"p95_generation_ms": 100.0},
    }


def test_operational_health_is_ok_without_threshold_breaches() -> None:
    result = assess_operational_health(_metrics(), OperationalThresholds())
    assert result == {"status": "ok", "alerts": []}


def test_missing_backup_is_critical() -> None:
    metrics = _metrics()
    metrics["backup"]["age_seconds"] = None
    result = assess_operational_health(metrics, OperationalThresholds())
    assert result["status"] == "critical"
    assert result["alerts"][0]["signal"] == "backup_missing"


def test_queue_lag_degrades_then_becomes_critical() -> None:
    metrics = _metrics()
    thresholds = OperationalThresholds(queue_warn_seconds=10, queue_critical_seconds=20)
    metrics["queue"]["oldest_runnable_pending_seconds"] = 15
    assert assess_operational_health(metrics, thresholds)["status"] == "degraded"
    metrics["queue"]["oldest_runnable_pending_seconds"] = 25
    assert assess_operational_health(metrics, thresholds)["status"] == "critical"


def test_threshold_configuration_rejects_inverted_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_QUEUE_WARN_SECONDS", "30")
    monkeypatch.setenv("OPS_QUEUE_CRITICAL_SECONDS", "10")
    with pytest.raises(RuntimeError, match="must exceed"):
        operational_thresholds()


class _FakeRow:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key: str):
        return getattr(self, key)


class _FakeConn:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def fetch(self, _sql: str, *_args) -> list:
        return self._rows


async def test_list_failed_summaries_returns_rows() -> None:
    created = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    conn = _FakeConn(
        [
            _FakeRow(
                code="0000000-00.0000.0.00.0001",
                court="TJSP",
                class_name="Execução Fiscal",
                model="claude-sonnet-5",
                prompt_version="v1",
                validation={"passed": False, "errors": ["hallucination"]},
                generation_ms=120,
                created_at=created,
            )
        ]
    )
    result = await list_failed_summaries(conn)
    assert len(result) == 1
    assert result[0]["code"] == "0000000-00.0000.0.00.0001"
    assert result[0]["validation"]["passed"] is False
    assert result[0]["created_at"] == "2026-01-15T12:00:00+00:00"


async def test_list_failed_summaries_parses_validation_text() -> None:
    conn = _FakeConn(
        [
            _FakeRow(
                code="0000000-00.0000.0.00.0002",
                court=None,
                class_name=None,
                model="model-x",
                prompt_version="v1",
                validation='{"passed": false}',
                generation_ms=None,
                created_at=None,
            )
        ]
    )
    result = await list_failed_summaries(conn)
    assert result[0]["validation"] == {"passed": False}
    assert result[0]["court"] is None
    assert result[0]["created_at"] is None
