from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest

import app.rag as rag
from app.api import app
from app.db import create_pool
from app.json_utils import decode_json_object
from app.migrations import migrate
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_secret_process_completes_locally_without_any_ai_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "secret-e2e-webhook")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants
            RESTART IDENTITY CASCADE
            """
        )

    request_id = f"req-secret-e2e-{uuid4()}"
    response_id = f"resp-secret-e2e-{uuid4()}"
    code = "0000000-00.2026.8.21.0997"
    secret_party = "PARTE ULTRASSECRETA"
    secret_subject = "ASSUNTO ULTRASSECRETO"
    secret_movement = "CONTEUDO PROCESSUAL ULTRASSECRETO"
    second_secret_movement = "SEGUNDO CONTEUDO SENSIVEL"
    forbidden_header_name = "NOME INTERNO SIGILOSO"
    forbidden_amount = "987654.32"

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("secret proceedings must not create an external provider client")

    async def forbidden_async_boundary(*args, **kwargs):
        raise AssertionError("secret proceedings must not cross an AI/retrieval provider boundary")

    monkeypatch.setattr(rag, "load_steps", forbidden_async_boundary)
    monkeypatch.setattr(rag, "ensure_step_embeddings", forbidden_async_boundary)
    monkeypatch.setattr(rag, "embed_query", forbidden_async_boundary)
    monkeypatch.setattr(rag, "vector_search", forbidden_async_boundary)
    monkeypatch.setattr(rag, "anthropic_client", forbidden_provider)
    monkeypatch.setattr(rag, "_generate", forbidden_async_boundary)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            lawsuit = await client.post(
                "/webhooks/judit/secret-e2e-webhook",
                json={
                    "callback_id": f"cb-secret-response-{uuid4()}",
                    "event_type": "response_created",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {
                        "request_id": request_id,
                        "response_id": response_id,
                        "response_type": "lawsuit",
                        "response_data": {
                            "code": code,
                            "classifications": [{"name": "Procedimento sob sigilo"}],
                            "courts": [{"name": "TJRS"}],
                            "secrecy_level": 2,
                            "name": forbidden_header_name,
                            "instance": 1,
                            "area": "Cível",
                            "state": "RS",
                            "amount": forbidden_amount,
                            "parties": [
                                {
                                    "name": secret_party,
                                    "side": "Active",
                                    "person_type": "Natural",
                                }
                            ],
                            "subjects": [{"code": "S-1", "name": secret_subject}],
                            "steps": [
                                {
                                    "step_id": "secret-step-1",
                                    "step_date": "2026-01-10T12:00:00Z",
                                    "step_type": "SEGREDO",
                                    "content": secret_movement,
                                },
                                {
                                    "step_id": "secret-step-2",
                                    "step_date": "2026-02-10T12:00:00Z",
                                    "step_type": "DECISÃO",
                                    "content": second_secret_movement,
                                },
                            ],
                        },
                        "tags": {"cached_response": False},
                    },
                },
            )
            assert lawsuit.status_code == 200

            completed = await client.post(
                "/webhooks/judit/secret-e2e-webhook",
                json={
                    "callback_id": f"cb-secret-completed-{uuid4()}",
                    "event_type": "request_completed",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {"status": "completed"},
                },
            )
            assert completed.status_code == 200

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

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                """
                SELECT id, code, class_name, current_version_id, secrecy_level,
                       parties, subjects, header
                FROM processes
                WHERE code = $1
                """,
                code,
            )
            assert process is not None
            assert process["current_version_id"] is not None
            assert int(process["secrecy_level"]) == 2

            step_count = await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE process_id = $1",
                process["id"],
            )
            embedded_count = await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE process_id = $1 AND embedding IS NOT NULL",
                process["id"],
            )
            assert step_count == 0
            assert embedded_count == 0

            # Restricted content is retained only in the immutable raw Judit source
            # according to retention policy. It is never promoted into normalized
            # process fields or the lexical/vector retrieval surface.
            source_payload = await conn.fetchval(
                "SELECT source_payload FROM process_versions WHERE id = $1",
                process["current_version_id"],
            )
            raw_source = json.dumps(source_payload, ensure_ascii=False, default=str)
            for restricted in (
                secret_party,
                secret_subject,
                secret_movement,
                second_secret_movement,
                forbidden_header_name,
                forbidden_amount,
            ):
                assert restricted in raw_source

            normalized = json.dumps(
                {
                    "parties": process["parties"],
                    "subjects": process["subjects"],
                    "header": process["header"],
                },
                ensure_ascii=False,
                default=str,
            )
            for restricted in (
                secret_party,
                secret_subject,
                secret_movement,
                second_secret_movement,
                forbidden_header_name,
                forbidden_amount,
            ):
                assert restricted not in normalized

            assert process["parties"] == []
            assert process["subjects"] == []
            header = decode_json_object(process["header"], label="secret process header")
            assert header == {"instance": 1, "area": "Cível", "state": "RS"}

            summary = await conn.fetchrow(
                """
                SELECT markdown, structured_output, validation, model, prompt_version
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process["id"],
                process["current_version_id"],
            )
            assert summary is not None
            validation = decode_json_object(summary["validation"], label="summary validation")
            assert validation["passed"] is True
            assert summary["model"] == rag.RESTRICTED_MODEL == "local-deterministic"
            assert summary["prompt_version"] == rag.RESTRICTED_PROMPT_VERSION
            assert rag.is_restricted_local_summary(summary, process=process) is True

            structured_output = decode_json_object(
                summary["structured_output"],
                label="restricted summary structured output",
            )
            assert structured_output["process"] == {
                "cnj": code,
                "class_name": "Procedimento sob sigilo",
                "court": None,
                "header": {"instance": 1, "area": "Cível", "state": "RS"},
                "parties": [],
                "representatives": [],
            }

            leaked_summary_text = {
                **structured_output,
                "summary": {
                    **structured_output["summary"],
                    "synthesis": secret_party,
                },
            }
            assert rag.is_restricted_local_summary(
                {**dict(summary), "structured_output": leaked_summary_text},
                process=process,
            ) is False

            leaked_parties = {
                **structured_output,
                "process": {
                    **structured_output["process"],
                    "parties": [{"name": secret_party}],
                },
            }
            assert rag.is_restricted_local_summary(
                {**dict(summary), "structured_output": leaked_parties},
                process=process,
            ) is False

            leaked_header = {
                **structured_output,
                "process": {
                    **structured_output["process"],
                    "header": {
                        **structured_output["process"]["header"],
                        "amount": forbidden_amount,
                    },
                },
            }
            assert rag.is_restricted_local_summary(
                {**dict(summary), "structured_output": leaked_header},
                process=process,
            ) is False

            wrong_court = {
                **structured_output,
                "process": {
                    **structured_output["process"],
                    "court": "TJRS",
                },
            }
            assert rag.is_restricted_local_summary(
                {**dict(summary), "structured_output": wrong_court},
                process=process,
            ) is False

            await conn.execute(
                """
                UPDATE process_summaries
                SET structured_output = structured_output || '{"debug":"must-not-publish"}'::jsonb
                WHERE process_id = $1 AND version_id = $2
                """,
                process["id"],
                process["current_version_id"],
            )
            tampered_summary = await conn.fetchrow(
                """
                SELECT structured_output, model, prompt_version
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process["id"],
                process["current_version_id"],
            )
            assert tampered_summary is not None
            assert rag.is_restricted_local_summary(
                tampered_summary, process=process
            ) is False

            markdown = summary["markdown"]
            assert "sigilo" in markdown.casefold()
            assert "Procedimento sob sigilo" in markdown
            assert "Instância: 1" in markdown
            assert "Área: Cível" in markdown
            assert "Estado: RS" in markdown
            for forbidden in (
                code,
                secret_party,
                secret_subject,
                secret_movement,
                second_secret_movement,
                forbidden_header_name,
                forbidden_amount,
            ):
                assert forbidden not in markdown

            states = await conn.fetch(
                """
                SELECT task_name, status
                FROM jobs
                WHERE idempotency_key IN ($1, $2)
                ORDER BY task_name
                """,
                f"judit-finalize:{request_id}",
                f"summary:{process['current_version_id']}",
            )
            assert {row["task_name"]: str(row["status"]) for row in states} == {
                "finalize_judit_request": "completed",
                "generate_process_summary": "completed",
            }
    finally:
        await pool.close()
