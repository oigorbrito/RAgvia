from __future__ import annotations

import asyncio
import json
import math
import os
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from app.http_safety import ResponseTooLargeError, read_bounded_response
from app.json_utils import loads_strict_json
from app.providers import ProviderSettings, call_with_retries
from app.retrieval import Step

COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
DEFAULT_COHERE_RERANKER_MODEL = "rerank-v4.0-pro"
COHERE_RERANK_MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(slots=True)
class CohereRerankerError(RuntimeError):
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


def _is_retryable_cohere_error(exc: Exception) -> bool:
    if isinstance(exc, CohereRerankerError) and exc.status_code is not None:
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    return isinstance(exc, (URLError, TimeoutError, socket.timeout))


def cohere_reranker_model() -> str:
    model = str(os.environ.get("COHERE_RERANKER_MODEL") or DEFAULT_COHERE_RERANKER_MODEL).strip()
    if not model:
        raise RuntimeError("COHERE_RERANKER_MODEL must not be empty")
    return model


def _api_key() -> str:
    value = str(os.environ.get("COHERE_API_KEY") or "").strip()
    if not value:
        raise RuntimeError("COHERE_API_KEY is required when Cohere reranking is active")
    return value


def _settings() -> ProviderSettings:
    return ProviderSettings.from_env(timeout_env="RERANKER_TIMEOUT_SECONDS")


def _post_rerank_sync(
    *,
    api_key: str,
    model: str,
    query: str,
    documents: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
    ).encode("utf-8")
    request = Request(
        COHERE_RERANK_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = read_bounded_response(
                response,
                max_bytes=COHERE_RERANK_MAX_RESPONSE_BYTES,
            )
    except ResponseTooLargeError as exc:
        raise CohereRerankerError(
            "Cohere rerank response exceeded safe size"
        ) from exc
    except HTTPError as exc:
        raise CohereRerankerError(
            f"Cohere rerank request failed with HTTP {exc.code}",
            status_code=int(exc.code),
        ) from exc
    except (URLError, TimeoutError, socket.timeout):
        raise

    try:
        decoded = loads_strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Cohere rerank response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Cohere rerank response must be an object")
    return decoded


def _decode_scores(payload: Any, *, steps: Sequence[Step]) -> dict[UUID, float]:
    if not isinstance(payload, dict):
        raise RuntimeError("Cohere rerank response must be an object")
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("Cohere rerank response is missing results")
    if len(results) != len(steps):
        raise RuntimeError(
            f"Cohere rerank returned {len(results)} results for {len(steps)} candidates"
        )

    scores: dict[UUID, float] = {}
    seen: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise RuntimeError("Cohere rerank response contains an invalid result")
        index = item.get("index")
        relevance_score = item.get("relevance_score")
        if not isinstance(index, int) or index < 0 or index >= len(steps) or index in seen:
            raise RuntimeError("Cohere rerank response contains an invalid index")
        try:
            score = float(relevance_score)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Cohere rerank response contains an invalid score") from exc
        if not math.isfinite(score):
            raise RuntimeError("Cohere rerank response contains a non-finite score")
        seen.add(index)
        scores[steps[index].id] = score
    return scores


class CohereRerankerScorer:
    """Explicit external scorer for Cohere Rerank v2."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = str(model or cohere_reranker_model()).strip()
        if not self.model:
            raise RuntimeError("reranker model must not be empty")

    async def __call__(self, query: str, steps: Sequence[Step]) -> dict[UUID, float]:
        if not query.strip():
            raise ValueError("reranker query must not be empty")
        if not steps:
            return {}

        documents = [step.searchable_text for step in steps]
        settings = _settings()
        api_key = _api_key()

        async def operation() -> dict[str, Any]:
            return await asyncio.to_thread(
                _post_rerank_sync,
                api_key=api_key,
                model=self.model,
                query=query,
                documents=documents,
                timeout_seconds=settings.timeout_seconds,
            )

        response = await call_with_retries(
            operation,
            is_retryable=_is_retryable_cohere_error,
            settings=settings,
        )
        return _decode_scores(response, steps=steps)
