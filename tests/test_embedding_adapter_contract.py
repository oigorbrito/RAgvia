from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.embeddings_bge as embeddings_bge
import app.embeddings_cohere as embeddings_cohere
from app.embeddings_bge import BGEEmbeddingEncoder
from app.embeddings_cohere import CohereEmbeddingEncoder


class FakeBGEModel:
    def encode_corpus(self, values, **kwargs):
        return {"dense_vecs": [[0.1] * 1024 for _ in values]}

    def encode_queries(self, values, **kwargs):
        return {"dense_vecs": [[0.2] * 1024 for _ in values]}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["bge", "cohere"])
async def test_adapters_share_document_query_dimension_contract(
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if provider == "bge":
        artifact = tmp_path / "bge-m3"
        artifact.mkdir()
        monkeypatch.setattr(
            embeddings_bge,
            "_load_bge_model",
            lambda **kwargs: FakeBGEModel(),
        )
        encoder: Any = BGEEmbeddingEncoder(artifact_path=str(artifact), device="cpu")
    else:
        monkeypatch.setenv("COHERE_API_KEY", "fake-cohere-key")
        monkeypatch.setattr(
            embeddings_cohere,
            "_post_embed_sync",
            lambda **kwargs: {
                "embeddings": {
                    "float": [[0.3] * 1024 for _ in kwargs["texts"]]
                }
            },
        )
        encoder = CohereEmbeddingEncoder()

    documents = await encoder.embed_documents(["movimento a", "movimento b"])
    query = await encoder.embed_query("consulta")

    assert encoder.space.provider == provider
    assert encoder.space.dimensions == 1024
    assert len(documents) == 2
    assert all(len(vector) == 1024 for vector in documents)
    assert len(query) == 1024
