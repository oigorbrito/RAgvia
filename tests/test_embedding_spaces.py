from __future__ import annotations

import pytest

from app.embedding_spaces import (
    BGE_MODEL,
    COHERE_MODEL,
    EMBEDDING_DIMENSIONS,
    EmbeddingSpace,
    active_embedding_space,
    assert_embedding_dimensions,
)


def test_bge_is_default_embedding_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("BGE_EMBEDDING_MODEL", raising=False)

    space = active_embedding_space()

    assert space == EmbeddingSpace(
        provider="bge",
        model=BGE_MODEL,
        dimensions=EMBEDDING_DIMENSIONS,
    )
    assert space.key == "bge:BAAI/bge-m3:1024"


def test_cohere_requires_explicit_external_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    monkeypatch.delenv("ALLOW_EXTERNAL_EMBEDDINGS", raising=False)

    with pytest.raises(RuntimeError, match="ALLOW_EXTERNAL_EMBEDDINGS=true"):
        active_embedding_space()

    monkeypatch.setenv("ALLOW_EXTERNAL_EMBEDDINGS", "true")
    space = active_embedding_space()
    assert space.provider == "cohere"
    assert space.model == COHERE_MODEL
    assert space.dimensions == 1024


def test_embedding_space_rejects_unknown_provider_or_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="unsupported EMBEDDING_PROVIDER"):
        active_embedding_space()

    monkeypatch.setenv("EMBEDDING_PROVIDER", "bge")
    monkeypatch.setenv("BGE_EMBEDDING_MODEL", "other-model")
    with pytest.raises(RuntimeError, match="unsupported BGE embedding model"):
        active_embedding_space()


def test_embedding_dimensions_are_space_bound() -> None:
    space = EmbeddingSpace(provider="bge", model=BGE_MODEL)
    assert_embedding_dimensions([0.0] * 1024, space=space)

    with pytest.raises(RuntimeError, match="expected 1024, got 1536"):
        assert_embedding_dimensions([0.0] * 1536, space=space)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_embedding_space_rejects_non_finite_vector_components(value: float) -> None:
    space = EmbeddingSpace(provider="bge", model=BGE_MODEL)
    vector = [0.0] * space.dimensions
    vector[17] = value
    with pytest.raises(RuntimeError, match="non-finite values"):
        assert_embedding_dimensions(vector, space=space)
