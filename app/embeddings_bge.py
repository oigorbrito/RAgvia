from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.embedding_spaces import BGE_MODEL, EmbeddingSpace, assert_embedding_dimensions


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


def _artifact_source(model: str, artifact_path: str | None) -> str:
    if artifact_path is None:
        return model
    path = Path(artifact_path).expanduser()
    if not path.exists():
        raise RuntimeError(f"BGE_EMBEDDING_PATH does not exist: {path}")
    if not path.is_dir():
        raise RuntimeError(f"BGE_EMBEDDING_PATH must be a directory: {path}")
    return str(path)


def _load_bge_model(*, model_source: str, use_fp16: bool, device: str | None) -> Any:
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError(
            "BGE embeddings require the optional 'embeddings' dependencies; "
            "install the project with rpy[embeddings]"
        ) from exc

    kwargs: dict[str, Any] = {
        "use_fp16": use_fp16,
        "pooling_method": "cls",
    }
    if device:
        kwargs["devices"] = device
    return BGEM3FlagModel(model_source, **kwargs)


def _dense_vectors(raw: Any, *, expected: int, space: EmbeddingSpace) -> list[list[float]]:
    if not isinstance(raw, dict) or "dense_vecs" not in raw:
        raise RuntimeError("BGE encoder returned no dense_vecs")
    dense = raw["dense_vecs"]
    try:
        rows = dense.tolist() if hasattr(dense, "tolist") else list(dense)
        vectors = [[float(value) for value in row] for row in rows]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("BGE encoder returned invalid dense vectors") from exc
    if len(vectors) != expected:
        raise RuntimeError(
            f"BGE encoder returned {len(vectors)} vectors for {expected} inputs"
        )
    for vector in vectors:
        assert_embedding_dimensions(vector, space=space)
    return vectors


class BGEEmbeddingEncoder:
    """Lazy local dense encoder for BAAI/bge-m3.

    Semantic identity stays pinned to BAAI/bge-m3 even when model bytes are
    supplied from a deployment-local artifact directory.
    """

    def __init__(
        self,
        *,
        model: str = BGE_MODEL,
        artifact_path: str | None = None,
        use_fp16: bool | None = None,
        device: str | None = None,
    ) -> None:
        self.space = EmbeddingSpace(provider="bge", model=model)
        if self.space.model != BGE_MODEL:
            raise RuntimeError(f"unsupported BGE embedding model: {self.space.model}")
        configured_path = (
            os.environ.get("BGE_EMBEDDING_PATH") if artifact_path is None else artifact_path
        )
        self.artifact_path = str(configured_path or "").strip() or None
        self.model_source = _artifact_source(self.space.model, self.artifact_path)
        self.use_fp16 = (
            _env_bool("BGE_EMBEDDING_USE_FP16", False)
            if use_fp16 is None
            else bool(use_fp16)
        )
        configured_device = os.environ.get("BGE_EMBEDDING_DEVICE") if device is None else device
        self.device = str(configured_device or "").strip() or None
        self._model: Any | None = None

    def _instance(self) -> Any:
        if self._model is None:
            self._model = _load_bge_model(
                model_source=self.model_source,
                use_fp16=self.use_fp16,
                device=self.device,
            )
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        values = [str(text) for text in texts]
        if not values:
            return []
        if any(not value.strip() for value in values):
            raise ValueError("embedding inputs must be non-empty text")
        model = self._instance()
        raw = await asyncio.to_thread(
            model.encode_corpus,
            values,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return _dense_vectors(raw, expected=len(values), space=self.space)

    async def embed_query(self, text: str) -> list[float]:
        value = str(text)
        if not value.strip():
            raise ValueError("embedding query must be non-empty text")
        model = self._instance()
        raw = await asyncio.to_thread(
            model.encode_queries,
            [value],
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return _dense_vectors(raw, expected=1, space=self.space)[0]
