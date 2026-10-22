from __future__ import annotations

import os

from app.reranker_bge import BGERerankerScorer
from app.reranker_cohere import CohereRerankerScorer
from app.reranking import RerankerScorer

BGE_RERANKER_PROVIDER = "bge"
COHERE_RERANKER_PROVIDER = "cohere"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def reranker_enabled() -> bool:
    return _env_bool("RERANKER_ENABLED", False)


def reranker_provider() -> str:
    provider = str(os.environ.get("RERANKER_PROVIDER") or BGE_RERANKER_PROVIDER).strip().casefold()
    if provider not in {BGE_RERANKER_PROVIDER, COHERE_RERANKER_PROVIDER}:
        raise RuntimeError(f"unsupported RERANKER_PROVIDER: {provider}")
    return provider


def configured_reranker_scorer() -> RerankerScorer | None:
    if not reranker_enabled():
        return None

    provider = reranker_provider()
    if provider == BGE_RERANKER_PROVIDER:
        return BGERerankerScorer()

    if not _env_bool("ALLOW_EXTERNAL_RERANKER", False):
        raise RuntimeError(
            "Cohere reranking requires explicit ALLOW_EXTERNAL_RERANKER=true authorization"
        )
    return CohereRerankerScorer()
