from __future__ import annotations

import asyncio
import math
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar, Any
from uuid import UUID
from types import SimpleNamespace

import anthropic
import openai

from app.generation_authorization import load_generation_permit

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


def anthropic_model(default: str) -> str:
    """Optional worker-only override; preserve volume routing when unset."""
    value = os.getenv("ANTHROPIC_MODEL")
    if value is None or value == "":
        return default
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise ValueError("ANTHROPIC_MODEL must be a bounded model identifier")
    return value


def anthropic_settings() -> ProviderSettings:
    return ProviderSettings.from_env(timeout_env="ANTHROPIC_TIMEOUT_SECONDS")


def embedding_settings() -> ProviderSettings:
    return ProviderSettings.from_env(timeout_env="EMBEDDING_TIMEOUT_SECONDS")


def anthropic_client(
    api_key: str, *, authorization_scope: tuple[UUID, UUID, UUID | None] | None = None
) -> Any:
    permit = load_generation_permit(authorization_scope)
    settings = anthropic_settings()
    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url="https://api.anthropic.com",
        timeout=settings.timeout_seconds,
        max_retries=0,
    )

    async def create(**request: Any) -> Any:
        await permit.reserve_message(request)
        return await client.messages.create(**request)

    # Only expose the bounded Messages operation, not raw SDK HTTP/tools methods.
    # The generation layer also needs this authorization-derived capability so it
    # does not request a correction message that the private permit did not allow.
    return SimpleNamespace(
        messages=SimpleNamespace(create=create),
        validation_regeneration_allowed=permit.max_messages > 1,
        close=client.close,
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
