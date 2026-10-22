from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.embedding_spaces import COHERE_MODEL, COHERE_PROVIDER, EmbeddingSpace, assert_embedding_dimensions
from app.http_safety import ResponseTooLargeError, read_bounded_response
from app.json_utils import loads_strict_json
from app.providers import call_with_retries, embedding_settings

COHERE_EMBED_URL = "https://api.cohere.com/v2/embed"
COHERE_DOCUMENT_INPUT_TYPE = "search_document"
COHERE_QUERY_INPUT_TYPE = "search_query"
COHERE_EMBED_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(slots=True)
class CohereProviderError(RuntimeError):
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


def _is_retryable_cohere_error(exc: Exception) -> bool:
    if isinstance(exc, CohereProviderError) and exc.status_code is not None:
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    return isinstance(exc, (URLError, TimeoutError, socket.timeout))


def _api_key() -> str:
    value = str(os.environ.get("COHERE_API_KEY") or "").strip()
    if not value:
        raise RuntimeError("COHERE_API_KEY is required when Cohere embeddings are active")
    return value


def _decode_vectors(payload: Any, *, expected_count: int, space: EmbeddingSpace) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise RuntimeError("Cohere embed response must be an object")
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, dict):
        raise RuntimeError("Cohere embed response is missing embeddings")
    raw = embeddings.get("float")
    if raw is None:
        raw = embeddings.get("float_")
    if not isinstance(raw, list):
        raise RuntimeError("Cohere embed response is missing float embeddings")
    if len(raw) != expected_count:
        raise RuntimeError(
            f"Cohere returned {len(raw)} vectors for {expected_count} inputs"
        )

    vectors: list[list[float]] = []
    for row in raw:
        if not isinstance(row, list):
            raise RuntimeError("Cohere embed response contains an invalid vector")
        try:
            vector = [float(value) for value in row]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Cohere embed response contains non-numeric values") from exc
        assert_embedding_dimensions(vector, space=space)
        vectors.append(vector)
    return vectors


def _post_embed_sync(
    *,
    api_key: str,
    texts: list[str],
    model: str,
    input_type: str,
    dimensions: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "texts": texts,
            "model": model,
            "input_type": input_type,
            "output_dimension": dimensions,
            "embedding_types": ["float"],
        }
    ).encode("utf-8")
    request = Request(
        COHERE_EMBED_URL,
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
                max_bytes=COHERE_EMBED_MAX_RESPONSE_BYTES,
            )
    except ResponseTooLargeError as exc:
        raise CohereProviderError(
            "Cohere embed response exceeded safe size"
        ) from exc
    except HTTPError as exc:
        raise CohereProviderError(
            f"Cohere embed request failed with HTTP {exc.code}",
            status_code=int(exc.code),
        ) from exc
    except (URLError, TimeoutError, socket.timeout):
        raise

    try:
        decoded = loads_strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Cohere embed response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Cohere embed response must be an object")
    return decoded


class CohereEmbeddingEncoder:
    """Explicit external adapter for Cohere Embed v4 semantic search.

    Deployment authorization is enforced by active_embedding_space(). This adapter
    never acts as a fallback and always requests the exact 1024-dimension float space.
    """

    def __init__(self, *, model: str = COHERE_MODEL) -> None:
        self.space = EmbeddingSpace(provider=COHERE_PROVIDER, model=model)
        if self.space.model != COHERE_MODEL:
            raise RuntimeError(f"unsupported Cohere embedding model: {self.space.model}")

    async def _embed(self, texts: Sequence[str], *, input_type: str) -> list[list[float]]:
        values = [str(text) for text in texts]
        if not values:
            return []
        if any(not value.strip() for value in values):
            raise ValueError("embedding inputs must be non-empty text")
        settings = embedding_settings()
        api_key = _api_key()

        async def operation() -> dict[str, Any]:
            return await asyncio.to_thread(
                _post_embed_sync,
                api_key=api_key,
                texts=values,
                model=self.space.model,
                input_type=input_type,
                dimensions=self.space.dimensions,
                timeout_seconds=settings.timeout_seconds,
            )

        response = await call_with_retries(
            operation,
            is_retryable=_is_retryable_cohere_error,
            settings=settings,
        )
        return _decode_vectors(response, expected_count=len(values), space=self.space)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, input_type=COHERE_DOCUMENT_INPUT_TYPE)

    async def embed_query(self, text: str) -> list[float]:
        value = str(text)
        if not value.strip():
            raise ValueError("embedding query must be non-empty text")
        return (await self._embed([value], input_type=COHERE_QUERY_INPUT_TYPE))[0]
