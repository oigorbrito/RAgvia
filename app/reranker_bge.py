from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from app.retrieval import Step

DEFAULT_BGE_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


def bge_reranker_model() -> str:
    model = str(os.environ.get("RERANKER_MODEL") or DEFAULT_BGE_RERANKER_MODEL).strip()
    if not model:
        raise RuntimeError("RERANKER_MODEL must not be empty")
    return model


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


def bge_reranker_enabled() -> bool:
    """Return whether local BGE reranking is explicitly enabled for this deployment."""
    return _env_bool("RERANKER_ENABLED", False)


def _artifact_source(model: str, artifact_path: str | None) -> str:
    if artifact_path is None:
        return model
    path = Path(artifact_path).expanduser()
    if not path.exists():
        raise RuntimeError(f"BGE_RERANKER_PATH does not exist: {path}")
    if not path.is_dir():
        raise RuntimeError(f"BGE_RERANKER_PATH must be a directory: {path}")
    return str(path)


def _load_flag_reranker(*, model_source: str, use_fp16: bool) -> Any:
    try:
        from FlagEmbedding import FlagReranker
    except ImportError as exc:
        raise RuntimeError(
            "BGE reranking requires the optional 'reranker' dependencies; "
            "install the project with rpy[reranker]"
        ) from exc
    return FlagReranker(model_source, use_fp16=use_fp16)


def _coerce_scores(raw: Any, *, expected: int) -> list[float]:
    if expected == 1 and isinstance(raw, (int, float)):
        values = [float(raw)]
    else:
        try:
            values = [float(value) for value in raw]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("BGE reranker returned invalid scores") from exc
    if len(values) != expected:
        raise RuntimeError(
            f"BGE reranker returned {len(values)} scores for {expected} candidates"
        )
    if any(not math.isfinite(value) for value in values):
        raise RuntimeError("BGE reranker returned a non-finite score")
    return values


class BGERerankerScorer:
    """Lazy local scorer for BAAI/bge-reranker-v2-m3 via FlagEmbedding.

    Model loading is deferred until the first long-process rerank so normal API
    startup and offline tests do not download or initialize the model. In
    production the model artifact should be pre-cached or provided through a
    local BGE_RERANKER_PATH.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        artifact_path: str | None = None,
        use_fp16: bool | None = None,
    ) -> None:
        self.model = str(model or bge_reranker_model()).strip()
        if not self.model:
            raise RuntimeError("reranker model must not be empty")
        configured_path = (
            os.environ.get("BGE_RERANKER_PATH") if artifact_path is None else artifact_path
        )
        self.artifact_path = str(configured_path or "").strip() or None
        self.model_source = _artifact_source(self.model, self.artifact_path)
        self.use_fp16 = _env_bool("RERANKER_USE_FP16", False) if use_fp16 is None else use_fp16
        self._reranker: Any | None = None

    def _instance(self) -> Any:
        if self._reranker is None:
            self._reranker = _load_flag_reranker(
                model_source=self.model_source,
                use_fp16=self.use_fp16,
            )
        return self._reranker

    async def __call__(self, query: str, steps: Sequence[Step]) -> dict[UUID, float]:
        if not query.strip():
            raise ValueError("reranker query must not be empty")
        if not steps:
            return {}
        pairs = [[query, step.searchable_text] for step in steps]
        reranker = self._instance()
        raw = await asyncio.to_thread(reranker.compute_score, pairs, normalize=True)
        values = _coerce_scores(raw, expected=len(steps))
        return {step.id: score for step, score in zip(steps, values, strict=True)}
