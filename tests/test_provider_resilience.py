from __future__ import annotations

from dataclasses import dataclass

import pytest

import app.providers as providers


class _TimeoutError(Exception):
    pass


class _ConnectionError(Exception):
    pass


@dataclass
class _StatusError(Exception):
    status_code: int


@pytest.fixture(autouse=True)
def _patch_provider_exception_types(monkeypatch):
    monkeypatch.setattr(providers.anthropic, "APITimeoutError", _TimeoutError)
    monkeypatch.setattr(providers.anthropic, "APIConnectionError", _ConnectionError)
    monkeypatch.setattr(providers.anthropic, "APIStatusError", _StatusError)
    monkeypatch.setattr(providers.openai, "APITimeoutError", _TimeoutError)
    monkeypatch.setattr(providers.openai, "APIConnectionError", _ConnectionError)
    monkeypatch.setattr(providers.openai, "APIStatusError", _StatusError)


@pytest.mark.asyncio
async def test_anthropic_timeout_is_retried_once() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _TimeoutError("timed out")
        return "ok"

    result = await providers.call_with_retries(
        operation,
        is_retryable=providers.is_retryable_anthropic_error,
        settings=providers.ProviderSettings(
            timeout_seconds=1,
            max_attempts=2,
            retry_backoff_seconds=0,
        ),
    )

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_transient_provider_status_is_retried(status_code: int) -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _StatusError(status_code)
        return "ok"

    result = await providers.call_with_retries(
        operation,
        is_retryable=providers.is_retryable_anthropic_error,
        settings=providers.ProviderSettings(
            timeout_seconds=1,
            max_attempts=2,
            retry_backoff_seconds=0,
        ),
    )

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_non_retryable_provider_error_fails_immediately() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise _StatusError(400)

    with pytest.raises(_StatusError):
        await providers.call_with_retries(
            operation,
            is_retryable=providers.is_retryable_anthropic_error,
            settings=providers.ProviderSettings(
                timeout_seconds=1,
                max_attempts=2,
                retry_backoff_seconds=0,
            ),
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_embedding_provider_connection_failure_is_bounded() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise _ConnectionError("provider unavailable")

    with pytest.raises(_ConnectionError):
        await providers.call_with_retries(
            operation,
            is_retryable=providers.is_retryable_openai_error,
            settings=providers.ProviderSettings(
                timeout_seconds=1,
                max_attempts=2,
                retry_backoff_seconds=0,
            ),
        )

    assert calls == 2


def test_provider_clients_disable_sdk_retries(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def fake_anthropic(**kwargs):
        captured["anthropic"] = kwargs
        return object()

    def fake_openai(**kwargs):
        captured["openai"] = kwargs
        return object()

    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", fake_anthropic)
    monkeypatch.setattr(providers.openai, "AsyncOpenAI", fake_openai)
    monkeypatch.setenv("ANTHROPIC_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "9")

    providers.anthropic_client("anthropic-key")
    providers.openai_client("openai-key")

    assert captured["anthropic"]["timeout"] == 7
    assert captured["anthropic"]["max_retries"] == 0
    assert captured["openai"]["timeout"] == 9
    assert captured["openai"]["max_retries"] == 0


def test_provider_timeout_must_fit_inside_worker_timeout(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_TASK_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("ANTHROPIC_TIMEOUT_SECONDS", "10")

    with pytest.raises(ValueError, match="lower than WORKER_TASK_TIMEOUT_SECONDS"):
        providers.anthropic_settings()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timeout_seconds", float("nan"), "finite number greater than zero"),
        ("timeout_seconds", float("inf"), "finite number greater than zero"),
        ("retry_backoff_seconds", float("nan"), "must be finite"),
        ("retry_backoff_seconds", float("inf"), "must be finite"),
    ],
)
def test_provider_settings_reject_non_finite_timing_controls(
    field: str,
    value: float,
    message: str,
) -> None:
    kwargs = {
        "timeout_seconds": 1.0,
        "max_attempts": 2,
        "retry_backoff_seconds": 0.1,
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        providers.ProviderSettings(**kwargs).validate()


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_provider_settings_reject_non_finite_worker_timeout_env(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WORKER_TASK_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="WORKER_TASK_TIMEOUT_SECONDS must be a finite"):
        providers.ProviderSettings(timeout_seconds=1).validate()
