from __future__ import annotations

import math
import os
from dataclasses import dataclass

BGE_PROVIDER = "bge"
COHERE_PROVIDER = "cohere"
BGE_MODEL = "BAAI/bge-m3"
COHERE_MODEL = "embed-v4.0"
EMBEDDING_DIMENSIONS = 1024


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    provider: str
    model: str
    dimensions: int = EMBEDDING_DIMENSIONS

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimensions}"


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


def active_embedding_space() -> EmbeddingSpace:
    """Resolve the one semantic embedding space active for this deployment.

    BGE-M3 is the default local space. Cohere is accepted only when deployment
    authorization is explicit; there is no automatic external fallback.
    """
    provider = str(os.environ.get("EMBEDDING_PROVIDER") or BGE_PROVIDER).strip().casefold()
    if provider == BGE_PROVIDER:
        model = str(os.environ.get("BGE_EMBEDDING_MODEL") or BGE_MODEL).strip()
        if model != BGE_MODEL:
            raise RuntimeError(
                f"unsupported BGE embedding model: {model}; expected {BGE_MODEL}"
            )
        return EmbeddingSpace(provider=BGE_PROVIDER, model=model)

    if provider == COHERE_PROVIDER:
        if not _env_bool("ALLOW_EXTERNAL_EMBEDDINGS", False):
            raise RuntimeError(
                "Cohere embeddings require ALLOW_EXTERNAL_EMBEDDINGS=true"
            )
        model = str(os.environ.get("COHERE_EMBEDDING_MODEL") or COHERE_MODEL).strip()
        if model != COHERE_MODEL:
            raise RuntimeError(
                f"unsupported Cohere embedding model: {model}; expected {COHERE_MODEL}"
            )
        return EmbeddingSpace(provider=COHERE_PROVIDER, model=model)

    raise RuntimeError(
        f"unsupported EMBEDDING_PROVIDER: {provider}; expected 'bge' or 'cohere'"
    )


def assert_embedding_dimensions(vector: list[float], *, space: EmbeddingSpace) -> None:
    if len(vector) != space.dimensions:
        raise RuntimeError(
            f"embedding dimension mismatch for {space.key}: "
            f"expected {space.dimensions}, got {len(vector)}"
        )
    if any(not math.isfinite(value) for value in vector):
        raise RuntimeError(f"embedding vector contains non-finite values for {space.key}")
