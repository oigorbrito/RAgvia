from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

import app.process_requests as process_requests_module
import app.rag as rag
from app.claim_evidence import build_material_claims
from app.summary_output import structured_summary_document
from app.api import app
from app.db import create_pool
from app.judit_client import JuditRequestError, JuditRequestResult
from app.migrations import migrate
from app.process_requests import request_process
from app.public_lifecycle import (
    IdempotencyConflictError,
    create_or_get_summary_request,
    get_summary_request_for_tenant,
    request_fingerprint,
    transition_summary_request,
)
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


async def _reset(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE public_summary_requests, jobs, judit_deliveries,
                     judit_request_completions, process_summaries, process_steps,
                     tenant_processes, tenant_judit_requests, access_log,
                     process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )


def _worker(pool) -> Worker:
    assert TEST_DATABASE_URL is not None
    return Worker(
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


@pytest.mark.asyncio
async def test_public_lifecycle_idempotency_states_and_terminals() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    try:
        await _reset(pool)
        tenant_id = uuid4()
        code = "0000000-00.0000.0.00.0301"
        payload = {"cnj": code, "force": False}
        fingerprint = request_fingerprint(payload)

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'lifecycle tenant')",
                tenant_id,
            )
            created_request, created = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=code,
                idempotency_key="idem-main",
                fingerprint=fingerprint,
            )
            assert created is True
            assert created_request.status == "queued"

            replay, replay_created = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=code,
                idempotency_key="idem-main",
                fingerprint=fingerprint,
            )
            assert replay_created is False
            assert replay.id == created_request.id

            with pytest.raises(IdempotencyConflictError):
                await create_or_get_summary_request(
                    conn,
                    tenant_id=tenant_id,
                    process_code=code,
                    idempotency_key="idem-main",
                    fingerprint=request_fingerprint({"cnj": code, "force": True}),
                )

            for status in ("fetching", "indexing", "generating", "validating", "completed"):
                current = await transition_summary_request(
                    conn,
                    request_id=created_request.id,
                    status=status,
                )
                assert current.status == status

            regressed = await transition_summary_request(
                conn,
                request_id=created_request.id,
                status="indexing",
            )
            assert regressed.status == "completed"
            assert await conn.fetchval(
                "SELECT completed_at IS NOT NULL FROM public_summary_requests WHERE id=$1",
                created_request.id,
            ) is True

            for index, terminal in enumerate(
                ("failed", "source_unavailable", "secrecy_blocked", "validation_failed"),
                start=1,
            ):
                request, _ = await create_or_get_summary_request(
                    conn,
                    tenant_id=tenant_id,
                    process_code=code,
                    idempotency_key=f"idem-terminal-{index}",
                    fingerprint=request_fingerprint({"cnj": code, "case": terminal}),
                )
                terminal_row = await transition_summary_request(
                    conn,
                    request_id=request.id,
                    status=terminal,
                    error_code=terminal,
                )
                assert terminal_row.status == terminal
                assert terminal_row.error_code == terminal
                assert await conn.fetchval(
                    "SELECT completed_at IS NOT NULL FROM public_summary_requests WHERE id=$1",
                    request.id,
                ) is True
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_public_lifecycle_runs_through_fake_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    app.state.webhook_tenant_id = None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "lifecycle-webhook")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")

    try:
        await _reset(pool)
        tenant_id = uuid4()
        code = "0000000-00.0000.0.00.0302"
        provider_request_id = f"req-lifecycle-{uuid4()}"
        response_id = f"resp-lifecycle-{uuid4()}"

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'pipeline tenant')",
                tenant_id,
            )
            public_request, _ = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=code,
                idempotency_key="idem-pipeline",
                fingerprint=request_fingerprint({"cnj": code}),
            )

        async def fake_create_lawsuit_request(requested_code: str) -> JuditRequestResult:
            assert requested_code == code
            return JuditRequestResult(request_id=provider_request_id)

        monkeypatch.setattr(
            process_requests_module,
            "create_lawsuit_request",
            fake_create_lawsuit_request,
        )

        result = await request_process(
            pool,
            tenant_id=tenant_id,
            code=code,
            public_summary_request_id=public_request.id,
        )
        assert result.created is True

        worker = _worker(pool)
        assert await worker.process_one() is True
        async with pool.acquire() as conn:
            fetching = await get_summary_request_for_tenant(
                conn,
                tenant_id=tenant_id,
                request_id=public_request.id,
            )
            assert fetching is not None
            assert fetching.status == "fetching"
            assert fetching.tenant_judit_request_id is not None

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response_created = await client.post(
                "/webhooks/judit/lifecycle-webhook",
                json={
                    "callback_id": f"cb-response-{uuid4()}",
                    "event_type": "response_created",
                    "reference_type": "request",
                    "reference_id": provider_request_id,
                    "payload": {
                        "request_id": provider_request_id,
                        "response_id": response_id,
                        "response_type": "lawsuit",
                        "response_data": {
                            "code": code,
                            "classifications": [{"name": "Procedimento Comum"}],
                            "courts": [{"name": "TJRS"}],
                            "parties": [],
                            "subjects": [{"code": "1", "name": "Obrigação"}],
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "step_date": "2026-01-10T12:00:00Z",
                                    "step_type": "DISTRIBUIÇÃO",
                                    "content": "Processo distribuído.",
                                },
                                {
                                    "step_id": "step-2",
                                    "step_date": "2026-02-10T12:00:00Z",
                                    "step_type": "CITAÇÃO",
                                    "content": "Citação registrada nos autos.",
                                },
                                {
                                    "step_id": "step-3",
                                    "step_date": "2026-03-10T12:00:00Z",
                                    "step_type": "SENTENÇA",
                                    "content": "Sentença registrada nos autos.",
                                },
                            ],
                        },
                        "tags": {"cached_response": False},
                    },
                },
            )
            assert response_created.status_code == 200
            completed = await client.post(
                "/webhooks/judit/lifecycle-webhook",
                json={
                    "callback_id": f"cb-completed-{uuid4()}",
                    "event_type": "request_completed",
                    "reference_type": "request",
                    "reference_id": provider_request_id,
                    "payload": {"status": "completed"},
                },
            )
            assert completed.status_code == 200

        assert await worker.process_one() is True
        async with pool.acquire() as conn:
            indexing = await get_summary_request_for_tenant(
                conn,
                tenant_id=tenant_id,
                request_id=public_request.id,
            )
            assert indexing is not None
            assert indexing.status == "indexing"
            assert indexing.process_id is not None
            assert indexing.version_id is not None

        async def fake_generate(client, context, validation_errors=None):
            _prime_fake_structured_summary(context)
            assert validation_errors is None
            async with pool.acquire() as conn:
                generating = await get_summary_request_for_tenant(
                    conn,
                    tenant_id=tenant_id,
                    request_id=public_request.id,
                )
                assert generating is not None
                assert generating.status == "generating"
            return f"""# Resumo do processo

<ProcessHeader className=\"process-header\">
- Processo: {code}
- Classe: Procedimento Comum
- Tribunal: TJRS
</ProcessHeader>

O processo contém registros de distribuição, citação e sentença.

## Classe
Procedimento Comum

## Assuntos
- Obrigação

## Movimentações
- Distribuição inicial.
- Citação registrada.
- Sentença registrada.
- Estado atual: o último movimento fornecido é uma sentença.

## Pontos de atenção
Nenhuma divergência objetiva identificada.
"""

        monkeypatch.setattr(rag, "_generate", fake_generate)
        assert await worker.process_one() is True
        assert await worker.process_one() is False

        async with pool.acquire() as conn:
            public_done = await get_summary_request_for_tenant(
                conn,
                tenant_id=tenant_id,
                request_id=public_request.id,
            )
            assert public_done is not None
            assert public_done.status == "completed"
            assert public_done.process_id is not None
            assert public_done.version_id is not None
            assert public_done.summary_id is not None
            assert await conn.fetchval(
                "SELECT COALESCE((validation->>'passed')::boolean, false) FROM process_summaries WHERE id=$1",
                public_done.summary_id,
            ) is True
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_source_failure_is_terminal_and_not_overwritten_by_worker_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    try:
        await _reset(pool)
        tenant_id = uuid4()
        code = "0000000-00.0000.0.00.0303"
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'source failure tenant')",
                tenant_id,
            )
            public_request, _ = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=code,
                idempotency_key="idem-source-failure",
                fingerprint=request_fingerprint({"cnj": code}),
            )

        async def rejected(_code: str):
            raise JuditRequestError("explicit rejection", retry_safe=True)

        monkeypatch.setattr(process_requests_module, "create_lawsuit_request", rejected)
        await request_process(
            pool,
            tenant_id=tenant_id,
            code=code,
            public_summary_request_id=public_request.id,
        )
        worker = _worker(pool)
        assert await worker.process_one() is True

        async with pool.acquire() as conn:
            terminal = await get_summary_request_for_tenant(
                conn,
                tenant_id=tenant_id,
                request_id=public_request.id,
            )
            assert terminal is not None
            assert terminal.status == "source_unavailable"
            assert terminal.error_code == "source_unavailable"
            assert await conn.fetchval(
                "SELECT status FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
                tenant_id,
                code,
            ) == "failed_retryable"
            assert await conn.fetchval(
                "SELECT status::text FROM jobs WHERE task_name='request_judit_process' ORDER BY created_at DESC LIMIT 1"
            ) == "dead"
    finally:
        await pool.close()
