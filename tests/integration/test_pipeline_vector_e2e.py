from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

import app.embeddings as embeddings
import app.rag as rag
from app.claim_evidence import build_material_claims
from app.summary_output import structured_summary_document
from app.api import app
from app.db import create_pool
from app.json_utils import decode_json_object
from app.migrations import migrate
from app.worker import Worker, WorkerSettings


def _prime_fake_structured_summary(context: dict) -> None:
    payload = {
        "synthesis": "Síntese factual de teste.",
        "timeline": [],
        "current_status": "Situação atual registrada nos autos.",
        "attention": ["Nenhuma divergência objetiva identificada."],
        "decisions": [],
        "deadlines": [],
        "related_processes": [],
        "attachments": [],
    }
    payload["claims"] = build_material_claims(
        payload,
        evidence_refs=[str(context["_process_evidence_ref"])],
    )
    context["_parsed_summary"] = payload
    context["_structured_summary"] = structured_summary_document(payload, context)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _vector(primary: float, secondary: float = 0.0) -> list[float]:
    vector = [0.0] * embeddings.VECTOR_DIMENSIONS
    vector[0] = primary
    vector[1] = secondary
    return vector


@pytest.mark.asyncio
async def test_large_process_uses_offline_embeddings_and_hybrid_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "vector-e2e-webhook")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-anthropic-key")
    # This test proves the configured-vector path. All embedding/provider calls
    # are replaced below with deterministic local fakes, so no network is used.
    monkeypatch.setenv("OPENAI_API_KEY", "offline-openai-key")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants
            RESTART IDENTITY CASCADE
            """
        )

    request_id = f"req-vector-e2e-{uuid4()}"
    response_id = f"resp-vector-e2e-{uuid4()}"
    code = "0000000-00.2026.8.21.0998"

    embedding_batches: list[list[str]] = []
    query_calls: list[str] = []
    vector_search_results: list[dict] = []
    generation_contexts: list[dict] = []

    async def fake_embed_texts(texts):
        batch = [str(text) for text in texts]
        embedding_batches.append(batch)
        vectors: list[list[float]] = []
        for text in batch:
            if "vetorial-prioritário" in text:
                vectors.append(_vector(1.0, 0.0))
            elif "sentença" in text.casefold():
                vectors.append(_vector(0.8, 0.2))
            else:
                vectors.append(_vector(0.0, 1.0))
        return vectors

    async def fake_embed_query(text: str):
        query_calls.append(text)
        return _vector(1.0, 0.0)

    real_vector_search = rag.vector_search

    async def recording_vector_search(conn, *, version_id, embedding, limit=30):
        result = await real_vector_search(
            conn,
            version_id=version_id,
            embedding=embedding,
            limit=limit,
        )
        vector_search_results.append(result)
        return result

    async def fake_generate(client, context, validation_errors=None):
        _prime_fake_structured_summary(context)
        assert validation_errors is None
        generation_contexts.append(context)
        assert context["code"] == code
        assert context["step_count"] == 45
        assert 1 <= len(context["steps"]) <= 20
        assert any("vetorial-prioritário" in step["text"] for step in context["steps"])
        return f"""# Resumo do processo

<ProcessHeader className=\"process-header\">
- Processo: {code}
- Classe: Procedimento Comum
- Tribunal: TJRS
</ProcessHeader>

O processo possui histórico extenso e foi recuperado por seleção híbrida offline.

## Classe
Procedimento Comum

## Assuntos
- Obrigação

## Movimentações
- Ajuizamento registrado.
- Decisão intermediária registrada.
- Sentença registrada.
- Estado atual: o último movimento fornecido integra a versão processual atual.

## Pontos de atenção
Nenhuma divergência objetiva identificada.
"""

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(rag, "embed_query", fake_embed_query)
    monkeypatch.setattr(rag, "vector_search", recording_vector_search)
    monkeypatch.setattr(rag, "_generate", fake_generate)
    monkeypatch.setattr(rag, "anthropic_client", lambda api_key: object())

    steps = []
    for index in range(1, 46):
        content = f"Movimento processual sintético número {index}."
        step_type = "ANDAMENTO"
        if index == 1:
            content = "Ajuizamento e distribuição da ação."
            step_type = "DISTRIBUIÇÃO"
        elif index == 17:
            content = "Evento vetorial-prioritário sem termo lexical da consulta."
        elif index == 30:
            content = "Decisão interlocutória sobre questão processual."
            step_type = "DECISÃO"
        elif index == 41:
            content = "Sentença registrada nos autos."
            step_type = "SENTENÇA"
        elif index == 45:
            content = "Último andamento da versão atual."

        steps.append(
            {
                "step_id": f"step-{index}",
                "step_date": f"2026-02-{((index - 1) % 28) + 1:02d}T12:00:00Z",
                "step_type": step_type,
                "content": content,
            }
        )

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response_created = await client.post(
                "/webhooks/judit/vector-e2e-webhook",
                json={
                    "callback_id": f"cb-vector-response-{uuid4()}",
                    "event_type": "response_created",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {
                        "request_id": request_id,
                        "response_id": response_id,
                        "response_type": "lawsuit",
                        "response_data": {
                            "code": code,
                            "classifications": [{"name": "Procedimento Comum"}],
                            "courts": [{"name": "TJRS"}],
                            "parties": [],
                            "subjects": [{"code": "1", "name": "Obrigação"}],
                            "steps": steps,
                        },
                        "tags": {"cached_response": False},
                    },
                },
            )
            assert response_created.status_code == 200

            request_completed = await client.post(
                "/webhooks/judit/vector-e2e-webhook",
                json={
                    "callback_id": f"cb-vector-completed-{uuid4()}",
                    "event_type": "request_completed",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {"status": "completed"},
                },
            )
            assert request_completed.status_code == 200

        worker = Worker(
            pool,
            WorkerSettings(
                database_url=TEST_DATABASE_URL,
                concurrency=1,
                heartbeat_interval_seconds=60,
                stale_after_seconds=120,
                task_timeout_seconds=20,
                reclaim_interval_seconds=60,
            ),
        )

        assert await worker.process_one() is True
        assert await worker.process_one() is True
        assert await worker.process_one() is False

        assert len(embedding_batches) == 1
        assert len(embedding_batches[0]) == 45
        assert query_calls == [rag.RETRIEVAL_QUERY]
        assert len(vector_search_results) == 1
        assert vector_search_results[0]
        assert len(generation_contexts) == 1

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                "SELECT id, current_version_id FROM processes WHERE code = $1",
                code,
            )
            assert process is not None
            assert process["current_version_id"] is not None

            step_count = await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE process_id = $1",
                process["id"],
            )
            embedded_count = await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE process_id = $1 AND embedding IS NOT NULL",
                process["id"],
            )
            assert step_count == 45
            assert embedded_count == 45

            prioritized_embedding = await conn.fetchval(
                """
                SELECT embedding IS NOT NULL
                FROM process_steps
                WHERE process_id = $1 AND text LIKE '%vetorial-prioritário%'
                """,
                process["id"],
            )
            assert prioritized_embedding is True

            summary = await conn.fetchrow(
                """
                SELECT markdown, validation, model, prompt_version
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process["id"],
                process["current_version_id"],
            )
            assert summary is not None
            validation = decode_json_object(summary["validation"], label="summary validation")
            assert validation["passed"] is True
            assert summary["model"] == "claude-sonnet-5"
            assert summary["prompt_version"] == "process-summary-v6"  # nosec B101 - pytest assertion

            job_states = await conn.fetch(
                """
                SELECT task_name, status
                FROM jobs
                WHERE idempotency_key IN ($1, $2)
                ORDER BY task_name
                """,
                f"judit-finalize:{request_id}",
                f"summary:{process['current_version_id']}",
            )
            assert {row["task_name"]: str(row["status"]) for row in job_states} == {
                "finalize_judit_request": "completed",
                "generate_process_summary": "completed",
            }
    finally:
        await pool.close()
