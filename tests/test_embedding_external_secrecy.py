from __future__ import annotations

from uuid import uuid4

import pytest

from app.embedding_runtime import ActiveEmbeddingRuntime
from app.embedding_spaces import COHERE_MODEL, EmbeddingSpace


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class SecretConnection:
    def __init__(self, secrecy_level: int):
        self.secrecy_level = secrecy_level
        self.fetch_calls = 0

    async def fetchval(self, query, *args):
        return self.secrecy_level

    async def fetch(self, query, *args):
        self.fetch_calls += 1
        return []


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeEncoder:
    def __init__(self):
        self.space = EmbeddingSpace(provider="cohere", model=COHERE_MODEL)
        self.calls = []

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[0.0] * 1024 for _ in texts]

    async def embed_query(self, text):
        return [0.0] * 1024


@pytest.mark.asyncio
async def test_secret_version_never_reaches_external_encoder() -> None:
    conn = SecretConnection(secrecy_level=1)
    encoder = FakeEncoder()
    runtime = ActiveEmbeddingRuntime(space=encoder.space, encoder=encoder)

    with pytest.raises(RuntimeError, match="secret process versions cannot use external embeddings"):
        await runtime.ensure_step_embeddings(FakePool(conn), version_id=uuid4())

    assert conn.fetch_calls == 0
    assert encoder.calls == []


@pytest.mark.asyncio
async def test_public_version_can_continue_to_embedding_selection() -> None:
    conn = SecretConnection(secrecy_level=0)
    encoder = FakeEncoder()
    runtime = ActiveEmbeddingRuntime(space=encoder.space, encoder=encoder)

    assert await runtime.ensure_step_embeddings(FakePool(conn), version_id=uuid4()) == 0
    assert conn.fetch_calls == 1
    assert encoder.calls == []
