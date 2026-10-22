from __future__ import annotations

from typing import Any

import pytest

import app.embeddings_cohere as embeddings_cohere
from app.embeddings_cohere import CohereEmbeddingEncoder, CohereProviderError


def _payload(count: int, dimensions: int = 1024) -> dict[str, Any]:
    return {"embeddings": {"float": [[0.25] * dimensions for _ in range(count)]}}


@pytest.mark.asyncio
async def test_cohere_uses_distinct_document_and_query_input_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(**kwargs):
        calls.append(dict(kwargs))
        return _payload(len(kwargs["texts"]))

    monkeypatch.setenv("COHERE_API_KEY", "fake-cohere-key")
    monkeypatch.setattr(embeddings_cohere, "_post_embed_sync", fake_post)
    encoder = CohereEmbeddingEncoder()

    documents = await encoder.embed_documents(["movimento um", "movimento dois"])
    query = await encoder.embed_query("sentença")

    assert encoder.space.key == "cohere:embed-v4.0:1024"
    assert len(documents) == 2
    assert len(query) == 1024
    assert [call["input_type"] for call in calls] == ["search_document", "search_query"]
    assert all(call["model"] == "embed-v4.0" for call in calls)
    assert all(call["dimensions"] == 1024 for call in calls)
    assert all(call["api_key"] == "fake-cohere-key" for call in calls)


@pytest.mark.asyncio
async def test_cohere_requires_key_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    encoder = CohereEmbeddingEncoder()
    with pytest.raises(RuntimeError, match="COHERE_API_KEY is required"):
        await encoder.embed_query("consulta")


@pytest.mark.asyncio
async def test_cohere_rejects_wrong_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "fake-cohere-key")
    monkeypatch.setattr(
        embeddings_cohere,
        "_post_embed_sync",
        lambda **kwargs: _payload(len(kwargs["texts"]), dimensions=1536),
    )
    with pytest.raises(RuntimeError, match="expected 1024, got 1536"):
        await CohereEmbeddingEncoder().embed_query("consulta")


@pytest.mark.asyncio
async def test_cohere_rejects_empty_inputs() -> None:
    encoder = CohereEmbeddingEncoder()
    assert await encoder.embed_documents([]) == []
    with pytest.raises(ValueError, match="non-empty text"):
        await encoder.embed_documents([" "])
    with pytest.raises(ValueError, match="non-empty text"):
        await encoder.embed_query("")


def test_cohere_retry_policy_is_status_bound() -> None:
    assert embeddings_cohere._is_retryable_cohere_error(
        CohereProviderError("rate limited", status_code=429)
    )
    assert embeddings_cohere._is_retryable_cohere_error(
        CohereProviderError("server", status_code=503)
    )
    assert not embeddings_cohere._is_retryable_cohere_error(
        CohereProviderError("bad request", status_code=400)
    )


def test_cohere_rejects_unknown_model() -> None:
    with pytest.raises(RuntimeError, match="unsupported Cohere embedding model"):
        CohereEmbeddingEncoder(model="other")


def test_cohere_embed_rejects_oversized_response(monkeypatch) -> None:
    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, amount: int = -1) -> bytes:
            if amount != embeddings_cohere.COHERE_EMBED_MAX_RESPONSE_BYTES + 1:
                raise AssertionError(f"unexpected read bound: {amount}")
            return b"x" * amount

    monkeypatch.setattr(
        embeddings_cohere,
        "urlopen",
        lambda request, timeout: OversizedResponse(),
    )
    with pytest.raises(CohereProviderError, match="exceeded safe size"):
        embeddings_cohere._post_embed_sync(
            api_key="synthetic-key",
            texts=["texto"],
            model="embed-v4.0",
            input_type="search_document",
            dimensions=1024,
            timeout_seconds=1,
        )


class _RawEmbedResponse:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"embeddings":{"float":[]},"embeddings":{"float":[]}}',
        b'{"embeddings":{"float":[[NaN]]}}',
    ],
)
def test_cohere_embed_rejects_ambiguous_provider_json(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    monkeypatch.setattr(
        embeddings_cohere,
        "urlopen",
        lambda request, timeout: _RawEmbedResponse(raw),
    )
    with pytest.raises(RuntimeError, match="not valid JSON"):
        embeddings_cohere._post_embed_sync(
            api_key="synthetic-key",
            texts=["texto"],
            model="embed-v4.0",
            input_type="search_document",
            dimensions=1024,
            timeout_seconds=1,
        )
