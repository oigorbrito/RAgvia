from __future__ import annotations

from uuid import uuid4

import pytest

import app.embedding_runtime as embedding_runtime
import app.embeddings as embeddings
import app.retrieval as retrieval


class FakeRuntime:
    def __init__(self) -> None:
        self.ensure_calls = []
        self.query_calls = []
        self.search_calls = []
        self.result_id = uuid4()
        self.encoder = self

    async def embed_documents(self, values):
        return [[1.0] * 1024 for _ in values]

    async def ensure_step_embeddings(self, pool, *, version_id):
        self.ensure_calls.append((pool, version_id))
        return 7

    async def embed_query(self, text):
        self.query_calls.append(text)
        return [0.5] * 1024

    async def vector_search(self, conn, *, version_id, embedding, limit):
        self.search_calls.append((conn, version_id, embedding, limit))
        return {self.result_id: 0.9}


class FakeFilteringConnection:
    def __init__(self, eligible_id):
        self.eligible_id = eligible_id
        self.fetch_calls = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return [{"id": self.eligible_id}]


def test_rollout_flag_defaults_off_and_preserves_legacy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBEDDING_SPACE_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert embedding_runtime.embedding_space_runtime_enabled() is False
    assert embeddings.vector_retrieval_configured() is False

    monkeypatch.setenv("OPENAI_API_KEY", "legacy-test-key")
    assert embeddings.vector_retrieval_configured() is True


@pytest.mark.asyncio
async def test_enabled_runtime_routes_query_reindex_and_search_without_legacy_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRuntime()
    monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(embeddings, "get_active_embedding_runtime", lambda: fake)
    monkeypatch.setattr(embedding_runtime, "get_active_embedding_runtime", lambda: fake)

    version_id = uuid4()
    pool = object()
    conn = FakeFilteringConnection(fake.result_id)

    assert embeddings.vector_retrieval_configured() is True
    assert await embeddings.ensure_step_embeddings(pool, version_id=version_id) == 7
    query_vector = await embeddings.embed_query("sentença")
    result = await retrieval.vector_search(
        conn,
        version_id=version_id,
        embedding=query_vector,
        limit=12,
    )

    assert fake.ensure_calls == [(pool, version_id)]
    assert fake.query_calls == ["sentença"]
    assert fake.search_calls == [(conn, version_id, [0.5] * 1024, 12)]
    assert result == {fake.result_id: 0.9}
    assert len(conn.fetch_calls) == 1


def test_cohere_runtime_requires_explicit_external_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    monkeypatch.delenv("ALLOW_EXTERNAL_EMBEDDINGS", raising=False)
    embedding_runtime.clear_embedding_runtime_cache()

    with pytest.raises(RuntimeError, match="ALLOW_EXTERNAL_EMBEDDINGS=true"):
        embedding_runtime.get_active_embedding_runtime()


def test_authorized_cohere_runtime_uses_separate_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = []

    class FakeCohereEncoder:
        def __init__(self, **kwargs):
            created.append(dict(kwargs))

    monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    monkeypatch.setenv("ALLOW_EXTERNAL_EMBEDDINGS", "true")
    monkeypatch.setattr(embedding_runtime, "CohereEmbeddingEncoder", FakeCohereEncoder)
    embedding_runtime.clear_embedding_runtime_cache()

    first = embedding_runtime.get_active_embedding_runtime()
    second = embedding_runtime.get_active_embedding_runtime()

    assert first is second
    assert first.space.key == "cohere:embed-v4.0:1024"
    assert created == [{"model": "embed-v4.0"}]


def test_bge_runtime_is_cached_by_deployment_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    created = []
    artifact = tmp_path / "bge-m3"
    artifact.mkdir()

    class FakeEncoder:
        def __init__(self, **kwargs):
            created.append(dict(kwargs))

    monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bge")
    monkeypatch.setenv("BGE_EMBEDDING_DEVICE", "cpu")
    monkeypatch.setenv("BGE_EMBEDDING_USE_FP16", "false")
    monkeypatch.setenv("BGE_EMBEDDING_PATH", str(artifact))
    monkeypatch.setattr(embedding_runtime, "BGEEmbeddingEncoder", FakeEncoder)
    embedding_runtime.clear_embedding_runtime_cache()

    first = embedding_runtime.get_active_embedding_runtime()
    second = embedding_runtime.get_active_embedding_runtime()

    assert first is second
    assert created == [
        {
            "model": "BAAI/bge-m3",
            "artifact_path": str(artifact),
            "use_fp16": False,
            "device": "cpu",
        }
    ]


def test_bge_runtime_cache_separates_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    created = []
    first_artifact = tmp_path / "first"
    second_artifact = tmp_path / "second"
    first_artifact.mkdir()
    second_artifact.mkdir()

    class FakeEncoder:
        def __init__(self, **kwargs):
            created.append(dict(kwargs))

    monkeypatch.setenv("EMBEDDING_SPACE_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bge")
    monkeypatch.setattr(embedding_runtime, "BGEEmbeddingEncoder", FakeEncoder)
    embedding_runtime.clear_embedding_runtime_cache()

    monkeypatch.setenv("BGE_EMBEDDING_PATH", str(first_artifact))
    first = embedding_runtime.get_active_embedding_runtime()
    monkeypatch.setenv("BGE_EMBEDDING_PATH", str(second_artifact))
    second = embedding_runtime.get_active_embedding_runtime()

    assert first is not second
    assert [item["artifact_path"] for item in created] == [
        str(first_artifact),
        str(second_artifact),
    ]
