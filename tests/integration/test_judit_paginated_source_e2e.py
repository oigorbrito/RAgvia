from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest

import app.rag as rag
from app.claim_evidence import build_material_claims
from app.summary_output import structured_summary_document
from app.api import app
from app.db import create_pool
from app.migrations import migrate
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _prime_fake_structured_summary(context: dict) -> None:
    payload = {
        "synthesis": "Síntese factual de teste.",
        "timeline": [],
        "current_status": "Situação atual registrada nos autos.",
        "attention": list(context.get("source_warnings", []))
        or ["Nenhuma divergência objetiva identificada."],
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


@pytest.mark.asyncio
async def test_paginated_request_uses_only_lawsuit_for_rag_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "paginated-e2e-webhook")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_request_completions, judit_deliveries,
                     process_summaries, process_steps, tenant_processes, access_log,
                     tenant_judit_requests, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    request_id = f"req-paginated-{uuid4()}"
    lawsuit_response_id = f"resp-lawsuit-{uuid4()}"
    summary_response_id = f"resp-summary-{uuid4()}"
    lawsuit_callback_id = f"cb-lawsuit-{uuid4()}"
    summary_callback_id = f"cb-summary-{uuid4()}"
    completion_callback_id = f"cb-completed-{uuid4()}"
    code = "0000000-00.2026.8.21.0111"
    external_summary = "SUMMARY_SENTINEL_MUST_NOT_REACH_RAG_CONTEXT"
    summary_personal_id = "55566677788"
    lawsuit_personal_id = "12345678901"
    party_personal_id = "11122233344"
    representative_personal_id = "98765432100"
    captured_provider_sources: list[str] = []

    async def fake_generate(client, context, validation_errors=None):
        source = rag._provider_source_text(context)
        captured_provider_sources.append(source)
        provider_payload = json.loads(source)
        provider_steps = provider_payload["movimentos"]
        temporal_facts = [step["source_temporal_facts"] for step in provider_steps]
        assert external_summary not in source
        assert summary_personal_id not in source
        assert lawsuit_personal_id not in source
        assert party_personal_id not in source
        assert "***.***.***-44" in source
        assert "[documento removido]" in source
        assert "ELETRÔNICA REFER" in source
        assert "ELETRÔNICAREFER" not in source
        assert "Sentença Proferida" in source
        assert "2026-06-10T09:00:00-03:00" in source
        assert "2026-06-11T09:00:00-03:00" in source
        assert "PRIVATE_PHASE_SENTINEL" not in source  # nosec B101 - pytest assertion
        assert "PRIVATE_STATUS_SENTINEL" not in source  # nosec B101 - pytest assertion
        assert "Magistrado Sintético" not in source  # nosec B101 - pytest assertion
        assert "Vara Cível Sintética" not in source  # nosec B101 - pytest assertion
        assert "Advogada Sintética" not in source  # nosec B101 - pytest assertion
        assert representative_personal_id not in source  # nosec B101 - pytest assertion
        assert {  # nosec B101 - pytest assertion
            "scheduled_hearing": {"date": "03/03/2027", "time": "15:00"}
        } in temporal_facts
        assert {"explicit_period": {"value": 15, "unit_text": "dias úteis"}} in temporal_facts  # nosec B101 - pytest assertion
        _prime_fake_structured_summary(context)
        return rag.render_structured_summary(context["_parsed_summary"], context)

    monkeypatch.setattr(rag, "_generate", fake_generate)

    lawsuit_body = {
        "callback_id": lawsuit_callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": lawsuit_response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "instance": 1,
                "area": "Cível",
                "justice_description": "Justiça Estadual",
                "phase": "PRIVATE_PHASE_SENTINEL",
                "status": "PRIVATE_STATUS_SENTINEL",
                "distribution_date": "2026-06-01T12:00:00Z",
                "judge": "Magistrado Sintético",
                "tribunal_acronym": "TJRS",
                "classifications": [{"name": "Procedimento Comum"}],
                "courts": [{"name": "Vara Cível Sintética"}],
                "parties": [
                    {
                        "name": "Parte Sintética",
                        "side": "Active",
                        "person_type": "Autor",
                        "main_document": party_personal_id,
                    },
                    {
                        "name": "Advogada Sintética",
                        "side": "Active",
                        "person_type": "ADVOGADA",
                        "main_document": representative_personal_id,
                        "represented_party": "Parte Sintética",
                    },
                ],
                "subjects": [{"code": "1", "name": "Obrigação"}],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_number": 10,
                        "step_date": "2026-06-10T12:00:00Z",
                        "step_type": "DISTRIBUIÇÃO",
                        "content": "10   ProcessoDistribuído ELETRÔNICAREFER nos autos.",
                    },
                    {
                        "step_id": "step-2",
                        "step_number": 20,
                        "step_date": "2026-06-11T12:00:00Z",
                        "step_type": "AUDIÊNCIA",
                        "content": "20 - Audiência de conciliação designada para 03/03/2027 às 15:00.",
                    },
                    {
                        "step_id": "step-3",
                        "step_number": 30,
                        "step_date": "2026-06-12T12:00:00Z",
                        "step_type": "INTIMAÇÃO",
                        "content": "30 - Intimação. Prazo: 15 dias úteis.",
                    },
                    {
                        "step_id": "step-4",
                        "step_number": 40,
                        "step_date": "2026-06-13T12:00:00Z",
                        "step_type": "SENTENÇA",
                        "content": f"40 - SentençaProferida CPF {lawsuit_personal_id}",
                    },
                ],
            },
            "tags": {"cached_response": False},
        },
    }
    summary_body = {
        "callback_id": summary_callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": summary_response_id,
            "response_type": "summary",
            "response_data": {
                "code": code,
                "summary": external_summary,
                "person_id": summary_personal_id,
            },
            "tags": {"cached_response": False},
        },
    }
    completion_body = {
        "callback_id": completion_callback_id,
        "event_type": "request_completed",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {"status": "completed"},
    }

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for body in (lawsuit_body, summary_body, completion_body):
                response = await client.post(
                    "/webhooks/judit/paginated-e2e-webhook",
                    json=body,
                )
                assert response.status_code == 200
            duplicate_delivery = await client.post(
                "/webhooks/judit/paginated-e2e-webhook",
                json=completion_body,
            )
            assert duplicate_delivery.status_code == 200  # nosec B101 - pytest assertion

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
        assert captured_provider_sources

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                """
                SELECT id, current_version_id, parties, representatives, header
                FROM processes WHERE code = $1
                """,
                code,
            )
            assert process is not None
            version_id = process["current_version_id"]
            assert version_id is not None

            normalized_parties = process["parties"]
            assert len(normalized_parties) == 1
            assert normalized_parties[0]["name"] == "Parte Sintética"
            assert normalized_parties[0]["masked_person_id"] == "***.***.***-44"
            assert party_personal_id not in json.dumps(normalized_parties, ensure_ascii=False)
            normalized_representatives = process["representatives"]
            assert len(normalized_representatives) == 1  # nosec B101 - pytest assertion
            assert normalized_representatives[0]["name"] == "Advogada Sintética"  # nosec B101 - pytest assertion
            assert normalized_representatives[0]["represents"] == "Parte Sintética"  # nosec B101 - pytest assertion
            assert representative_personal_id not in json.dumps(  # nosec B101 - pytest assertion
                normalized_representatives, ensure_ascii=False
            )
            assert normalized_representatives[0]["masked_person_id"] == "***.***.***-00"  # nosec B101 - pytest assertion
            persisted_header = process["header"]
            assert persisted_header["phase"] == "PRIVATE_PHASE_SENTINEL"  # nosec B101 - pytest assertion
            assert persisted_header["status"] == "PRIVATE_STATUS_SENTINEL"  # nosec B101 - pytest assertion
            assert persisted_header["distribution_date"] == "2026-06-01T12:00:00Z"  # nosec B101 - pytest assertion
            assert persisted_header["judge"] == "Magistrado Sintético"  # nosec B101 - pytest assertion
            assert persisted_header["court_unit"] == "Vara Cível Sintética"  # nosec B101 - pytest assertion
            assert persisted_header["area"] == "Cível"  # nosec B101 - pytest assertion

            version = await conn.fetchrow(
                """
                SELECT source_payload, judit_response_id, judit_request_id
                FROM process_versions
                WHERE id = $1
                """,
                version_id,
            )
            assert version is not None  # nosec B101 - pytest assertion
            rendered_source = json.dumps(version["source_payload"], ensure_ascii=False)
            assert version["judit_response_id"] == lawsuit_response_id  # nosec B101 - pytest assertion
            assert version["judit_request_id"] == request_id  # nosec B101 - pytest assertion
            assert external_summary not in rendered_source  # nosec B101 - pytest assertion
            assert summary_personal_id not in rendered_source  # nosec B101 - pytest assertion
            assert party_personal_id in rendered_source  # nosec B101 - pytest assertion
            assert "ELETRÔNICAREFER" in rendered_source  # nosec B101 - pytest assertion

            deliveries = await conn.fetch(
                """
                SELECT callback_id, raw_payload
                FROM judit_deliveries
                WHERE request_id = $1
                ORDER BY callback_id
                """,
                request_id,
            )
            assert len(deliveries) == 3  # nosec B101 - pytest assertion
            audit_payload = "\n".join(
                json.dumps(row["raw_payload"], ensure_ascii=False) for row in deliveries
            )
            assert external_summary in audit_payload  # nosec B101 - pytest assertion
            assert summary_personal_id in audit_payload  # nosec B101 - pytest assertion

            steps = await conn.fetch(
                """
                SELECT step_number, title, text, metadata
                FROM process_steps
                WHERE version_id = $1
                ORDER BY step_number
                """,
                version_id,
            )
            assert [row["step_number"] for row in steps] == [1, 2, 3, 4]  # nosec B101 - pytest assertion
            assert [row["metadata"]["source_step_number"] for row in steps] == [10, 20, 30, 40]  # nosec B101 - pytest assertion
            assert steps[0]["text"] == "Processo Distribuído ELETRÔNICA REFER nos autos."  # nosec B101 - pytest assertion
            assert steps[1]["text"] == "Audiência de conciliação designada para 03/03/2027 às 15:00."  # nosec B101 - pytest assertion
            assert steps[2]["text"] == "Intimação. Prazo: 15 dias úteis."  # nosec B101 - pytest assertion
            assert steps[3]["text"] == "Sentença Proferida CPF [documento removido]"  # nosec B101 - pytest assertion
            assert steps[0]["metadata"]["occurred_at_sao_paulo"] == "2026-06-10T09:00:00-03:00"  # nosec B101 - pytest assertion
            assert steps[1]["metadata"]["occurred_at_sao_paulo"] == "2026-06-11T09:00:00-03:00"  # nosec B101 - pytest assertion
            assert steps[2]["metadata"]["occurred_at_sao_paulo"] == "2026-06-12T09:00:00-03:00"  # nosec B101 - pytest assertion
            assert steps[3]["metadata"]["occurred_at_sao_paulo"] == "2026-06-13T09:00:00-03:00"  # nosec B101 - pytest assertion
            assert external_summary not in "\n".join(row["text"] for row in steps)  # nosec B101 - pytest assertion
            assert summary_personal_id not in "\n".join(row["text"] for row in steps)  # nosec B101 - pytest assertion
            assert party_personal_id not in "\n".join(row["text"] for row in steps)  # nosec B101 - pytest assertion

            generation_jobs = await conn.fetch(
                """
                SELECT status, payload
                FROM jobs
                WHERE task_name = 'generate_process_summary'
                """
            )
            assert len(generation_jobs) == 1  # nosec B101 - pytest assertion
            assert str(generation_jobs[0]["status"]) == "completed"  # nosec B101 - pytest assertion
            job_payload = json.dumps(generation_jobs[0]["payload"], ensure_ascii=False)
            assert external_summary not in job_payload  # nosec B101 - pytest assertion
            assert summary_personal_id not in job_payload  # nosec B101 - pytest assertion
            assert party_personal_id not in job_payload  # nosec B101 - pytest assertion

            summary_rows = await conn.fetchval(
                "SELECT count(*) FROM process_summaries WHERE process_id = $1",
                process["id"],
            )
            assert summary_rows == 1  # nosec B101 - pytest assertion
            stored_summary = await conn.fetchrow(
                """
                SELECT markdown, validation, version_id
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process["id"],
                version_id,
            )
            assert stored_summary is not None  # nosec B101 - pytest assertion
            assert stored_summary["version_id"] == version_id  # nosec B101 - pytest assertion
            assert stored_summary["validation"]["passed"] is True, stored_summary["validation"]  # nosec B101 - pytest assertion
            assert "## Representantes" in stored_summary["markdown"]  # nosec B101 - pytest assertion
            assert "Advogada Sintética (representa: Parte Sintética)" in stored_summary["markdown"]  # nosec B101 - pytest assertion
            assert "Fase registrada: PRIVATE_PHASE_SENTINEL" in stored_summary["markdown"]  # nosec B101 - pytest assertion
            assert "Situação registrada: PRIVATE_STATUS_SENTINEL" in stored_summary["markdown"]  # nosec B101 - pytest assertion
            assert "***.***.***-00" not in stored_summary["markdown"]  # nosec B101 - pytest assertion
            assert representative_personal_id not in stored_summary["markdown"]  # nosec B101 - pytest assertion

            tenant_id = uuid4()
            bearer_token = "paginated-e2e-tenant-token"  # nosec B105 - synthetic test credential
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'paginated e2e tenant')",
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
                tenant_id,
                process["id"],
            )
            app.state.bearer_tokens = {bearer_token: tenant_id}
            monkeypatch.setenv(
                "RPY_BEARER_TOKENS", json.dumps({bearer_token: str(tenant_id)})
            )

        auth_headers = {"Authorization": f"Bearer {bearer_token}"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            legacy_response = await client.get(
                f"/processes/{code}", headers=auth_headers
            )
            assert legacy_response.status_code == 200  # nosec B101 - pytest assertion
            legacy_payload = legacy_response.json()
            assert legacy_payload["summary_status"] == "available"  # nosec B101 - pytest assertion
            assert legacy_payload["iaSummary"] == legacy_payload["summary"]["markdown"]  # nosec B101 - pytest assertion
            assert "Advogada Sintética (representa: Parte Sintética)" in legacy_payload["iaSummary"]  # nosec B101 - pytest assertion

            jsx_response = await client.get(
                f"/v1/processos/{code}/resumo?format=jsx", headers=auth_headers
            )
            assert jsx_response.status_code == 200  # nosec B101 - pytest assertion
            assert isinstance(jsx_response.json()["iaSummary"], str)  # nosec B101 - pytest assertion

            json_response = await client.get(
                f"/v1/processos/{code}/resumo?format=json", headers=auth_headers
            )
            assert json_response.status_code == 200  # nosec B101 - pytest assertion
            structured = json_response.json()["iaSummary"]
            assert structured["schema_version"] == 3  # nosec B101 - pytest assertion
            assert structured["process"]["header"]["status"] == "PRIVATE_STATUS_SENTINEL"  # nosec B101 - pytest assertion
            assert structured["process"]["representatives"][0]["represents"] == "Parte Sintética"  # nosec B101 - pytest assertion
            assert representative_personal_id not in json.dumps(structured, ensure_ascii=False)  # nosec B101 - pytest assertion

            repeated_read = await client.get(
                f"/v1/processos/{code}/resumo?format=json", headers=auth_headers
            )
            assert repeated_read.status_code == 200  # nosec B101 - pytest assertion
            assert repeated_read.json()["iaSummary"] == structured  # nosec B101 - pytest assertion

        async with pool.acquire() as conn:
            assert await conn.fetchval(  # nosec B101 - pytest assertion
                "SELECT count(*) FROM process_summaries WHERE process_id = $1",
                process["id"],
            ) == 1
            assert await conn.fetchval(  # nosec B101 - pytest assertion
                "SELECT count(*) FROM jobs WHERE task_name='generate_process_summary'"
            ) == 1
    finally:
        await pool.close()
