from __future__ import annotations

import pytest

from app.worker import WorkerSettings


def test_worker_settings_reject_non_positive_values() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        WorkerSettings(database_url="postgresql://test", concurrency=0).validate()
    with pytest.raises(ValueError, match="heartbeat_interval_seconds"):
        WorkerSettings(
            database_url="postgresql://test", heartbeat_interval_seconds=0
        ).validate()
    with pytest.raises(ValueError, match="stale_after_seconds"):
        WorkerSettings(database_url="postgresql://test", stale_after_seconds=0).validate()
    with pytest.raises(ValueError, match="task_timeout_seconds"):
        WorkerSettings(database_url="postgresql://test", task_timeout_seconds=0).validate()


def test_worker_settings_require_stale_window_larger_than_heartbeat() -> None:
    with pytest.raises(ValueError, match="stale_after_seconds"):
        WorkerSettings(
            database_url="postgresql://test",
            heartbeat_interval_seconds=10,
            stale_after_seconds=10,
        ).validate()


def test_worker_settings_accept_safe_defaults() -> None:
    settings = WorkerSettings(database_url="postgresql://test")
    assert settings.validate() is settings
