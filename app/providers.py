from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import anthropic
import openai

T = TypeVar("T")

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 15.0
DEFAULT_PROVIDER_MAX_ATTEMPTS = 2
DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS = 0.25
_RETRYABLE_STATUS_CODES = {408, 409, 429}


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_PROVIDER_MAX_ATTEMPTS
    retry_backoff_seconds: float = DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS

    def validate(self) -> "ProviderSettings":
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError(
                "provider timeout_seconds must be a finite number greater than zero"
            )
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("provider max_attempts must be between 1 and 3")
        if not math.isfinite(self.retry_backoff_seconds) or self.retry_backoff_seconds < 0:
            raise ValueError(
                "provider retry_backoff_seconds must be finite and cannot be negative"
            )

        worker_timeout = os.getenv("WORKER_TASK_TIMEOUT_SECONDS")
        if worker_timeout is not None:
            parsed_worker_timeout = float(worker_timeout)
            if not math.isfinite(parsed_worker_timeout) or parsed_worker_timeout <= 0:
                raise ValueError(
                    "WORKER_TASK_TIMEOUT_SECONDS must be a finite number greater than zero"
                )
            if self.timeout_seconds >= parsed_worker_timeout:
                raise ValueError(
                    "provider timeout must be lower than WORKER_TASK_TIMEOUT_SECONDS"
                )
        return self

    @classmethod
    def from_env(cls, *, timeout_env: str) -> "ProviderSettings":
        return cls(
            timeout_seconds=float(
                os.getenv(timeout_env, str(DEFAULT_PROVIDER_TIMEOUT_SECONDS))
            ),
            max_attempts=int(
                os.getenv("PROVIDER_MAX_ATTEMPTS", str(DEFAULT_PROVIDER_MAX_ATTEMPTS))
            ),
            retry_backoff_seconds=float(
                os.getenv(
                    "PROVIDER_RETRY_BACKOFF_SECONDS",
                    str(DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS),
                )
            ),
        ).validate()


def anthropic_settings() -> ProviderSettings:
    return ProviderSettings.from_env(timeout_env="ANTHROPIC_TIMEOUT_SECONDS")


def embedding_settings() -> ProviderSettings:
    return ProviderSettings.from_env(timeout_env="EMBEDDING_TIMEOUT_SECONDS")


def anthropic_client(api_key: str) -> anthropic.AsyncAnthropic:
    settings = anthropic_settings()
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=settings.timeout_seconds,
        max_retries=0,
    )


def openai_client(api_key: str) -> openai.AsyncOpenAI:
    settings = embedding_settings()
    return openai.AsyncOpenAI(
        api_key=api_key,
        timeout=settings.timeout_seconds,
        max_retries=0,
    )


def _status_is_retryable(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500


def is_retryable_anthropic_error(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return _status_is_retryable(int(exc.status_code))
    return False


def is_retryable_openai_error(exc: Exception) -> bool:
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return _status_is_retryable(int(exc.status_code))
    return False


async def call_with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool],
    settings: ProviderSettings,
) -> T:
    """Retry a provider call locally without competing with queue-level retries."""
    for attempt in range(1, settings.max_attempts + 1):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt >= settings.max_attempts or not is_retryable(exc):
                raise
            delay = settings.retry_backoff_seconds * (2 ** (attempt - 1))
            if delay > 0:
                await asyncio.sleep(delay)

    raise AssertionError("provider retry loop exhausted unexpectedly")
