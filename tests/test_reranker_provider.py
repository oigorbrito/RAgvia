import pytest

import app.reranker_provider as provider
from app.reranker_bge import BGERerankerScorer
from app.reranker_cohere import CohereRerankerScorer


def test_disabled_reranker_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_ENABLED", "false")

    assert provider.configured_reranker_scorer() is None


def test_bge_is_default_enabled_provider(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.delenv("RERANKER_PROVIDER", raising=False)

    scorer = provider.configured_reranker_scorer()

    assert isinstance(scorer, BGERerankerScorer)


def test_cohere_requires_explicit_external_authorization(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_PROVIDER", "cohere")
    monkeypatch.setenv("ALLOW_EXTERNAL_RERANKER", "false")

    with pytest.raises(RuntimeError, match="ALLOW_EXTERNAL_RERANKER=true"):
        provider.configured_reranker_scorer()


def test_cohere_can_be_selected_when_explicitly_authorized(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_PROVIDER", "cohere")
    monkeypatch.setenv("ALLOW_EXTERNAL_RERANKER", "true")

    scorer = provider.configured_reranker_scorer()

    assert isinstance(scorer, CohereRerankerScorer)


def test_unknown_reranker_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_PROVIDER", "other")

    with pytest.raises(RuntimeError, match="unsupported RERANKER_PROVIDER"):
        provider.configured_reranker_scorer()
