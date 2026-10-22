from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from app.claim_evidence import (
    build_material_claims,
    evidence_catalog,
    process_evidence_ref,
    validate_claim_evidence,
)
from app.db import create_pool
from app.judit_tasks import _complete_from_current_summary, finalize_judit_request_task
from app.migrations import migrate
from app.processes import stage_version
from app.public_lifecycle import (
    create_or_get_summary_request,
    link_summary_request_to_acquisition,
    request_fingerprint,
    transition_summary_request,
)
from app.public_lifecycle_worker import reconcile_generation_result
from app.rag import _persist_summary
from app.summary_output import structured_summary_document
from app.summary_policy import RESTRICTED_MODEL, RESTRICTED_PROMPT_VERSION

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def _restricted_structured_output(code: str) -> dict:
    return structured_summary_document(
        {
            "synthesis": "Os detalhes processuais foram restringidos por sigilo.",
            "timeline": [],
            "current_status": (
                "O contexto público disponível está limitado pelos dados permitidos "
                "para processo sigiloso."
            ),
            "attention": ["Processo com detalhes restringidos por sigilo."],
            "decisions": [],
            "deadlines": [],
            "related_processes": [],
            "attachments": [],
        },
        {
            "code": code,
            "class_name": None,
            "court": None,
            "header": {},
            "parties": [],
        },
    )


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _lawsuit_event(
    *,
    request_id: str,
    response_id: str,
    callback_id: str,
    code: str,
    class_name: str = "Procedimento Comum",
    extra_step: bool = False,
    attachments: list[dict] | None = None,
) -> dict:
    steps = [
        {
            "step_id": "step-1",
            "step_number": 10,
            "step_date": "2026-06-10T12:00:00Z",
            "step_type": "CITAÇÃO",
            "content": "10 - CitaçãoRealizada",
            "private": False,
        }
    ]
    if extra_step:
        steps.append(
            {
                "step_id": "step-2",
                "step_number": 20,
                "step_date": "2026-06-11T12:00:00Z",
                "step_type": "SENTENÇA",
                "content": "20 - SentençaProferida",
                "private": False,
            }
        )
    return {
        "callback_id": callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "transport_nonce": str(uuid4()),
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "instance": 1,
                "classifications": [{"name": class_name}],
                "courts": [{"name": "TJRS"}],
                "parties": [],
                "subjects": [{"code": "1", "name": "Obrigação"}],
                "steps": steps,
                "attachments": attachments or [],
            },
            "tags": {"cached_response": False},
        },
    }


async def _stage(pool, event: dict) -> tuple:
    payload = event["payload"]
    data = payload["response_data"]
    async with pool.acquire() as conn:
        return await stage_version(
            conn,
            code=data["code"],
            source_request_id=payload["response_id"],
            cached_response=False,
            payload=event,
            judit_request_id=payload["request_id"],
            judit_response_id=payload["response_id"],
            judit_callback_id=event["callback_id"],
        )


@pytest.mark.asyncio
async def test_semantically_equal_response_reuses_current_version_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, process_summaries, process_steps, tenant_processes,
                     access_log, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    code = "0000000-00.2026.8.21.0136"
    first_request = f"req-first-{uuid4()}"
    first_response = f"resp-first-{uuid4()}"
    _, first_version = await _stage(
        pool,
        _lawsuit_event(
            request_id=first_request,
            response_id=first_response,
            callback_id=f"cb-first-{uuid4()}",
            code=code,
        ),
    )
    first_result = await finalize_judit_request_task({"request_id": first_request})
    assert first_result["status"] == "finalized"
    assert first_result["promoted"] is True
    assert first_result["summary_enqueued"] is True

    async with pool.acquire() as conn:
        process = await conn.fetchrow(
            """
            SELECT id, code, class_name, court, header, parties
            FROM processes
            WHERE code = $1
            """,
            code,
        )
        assert process is not None
        process_id = process["id"]
        header = process["header"]
        parties = process["parties"]
        if isinstance(header, str):
            header = json.loads(header)
        if isinstance(parties, str):
            parties = json.loads(parties)
        claim_context = {
            "code": process["code"],
            "class_name": process["class_name"],
            "court": process["court"],
            "header": header,
            "parties": parties,
            "_process_evidence_ref": process_evidence_ref(first_version),
            "_selected_sources": [],
            "_attachment_sources": [],
        }
        claim_payload = {
            "synthesis": "Resumo sintético",
            "timeline": [],
            "current_status": "Situação atual registrada.",
            "attention": ["Nenhum ponto adicional de atenção."],
            "decisions": [],
            "deadlines": [],
            "related_processes": [],
            "attachments": [],
        }
        claim_payload["claims"] = build_material_claims(
            claim_payload,
            evidence_refs=[claim_context["_process_evidence_ref"]],
        )
        claims, claim_errors = validate_claim_evidence(
            claim_payload,
            claim_context,
        )
        assert claim_errors == []
        assert await _persist_summary(
            conn,
            process_id=process_id,
            version_id=first_version,
            text="# resumo sintético",
            validation={"passed": True, "errors": []},
            generation_ms=1,
            structured_output=structured_summary_document(
                claim_payload,
                claim_context,
            ),
            claims=claims,
            evidence_sources=evidence_catalog(claim_context),
        )

    duplicate_request = f"req-duplicate-{uuid4()}"
    duplicate_response = f"resp-duplicate-{uuid4()}"
    _, duplicate_version = await _stage(
        pool,
        _lawsuit_event(
            request_id=duplicate_request,
            response_id=duplicate_response,
            callback_id=f"cb-duplicate-{uuid4()}",
            code=code,
        ),
    )
    duplicate_result = await finalize_judit_request_task({"request_id": duplicate_request})

    assert duplicate_result["status"] == "finalized_unchanged"
    assert duplicate_result["promoted"] is False
    assert duplicate_result["summary_enqueued"] is False
    assert duplicate_result["equivalent_to_version_id"] == str(first_version)

    async with pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE id = $1", process_id
        )
        duplicate = await conn.fetchrow(
            """
            SELECT finalized, semantic_fingerprint, semantic_schema_version,
                   equivalent_to_version_id, source_payload
            FROM process_versions WHERE id = $1
            """,
            duplicate_version,
        )
        duplicate_steps = await conn.fetchval(
            "SELECT count(*) FROM process_steps WHERE version_id = $1",
            duplicate_version,
        )
        generation_jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name = 'generate_process_summary'"
        )
        summaries = await conn.fetchval(
            "SELECT count(*) FROM process_summaries WHERE process_id = $1", process_id
        )

    assert current_version == first_version
    assert duplicate["finalized"] is True
    assert duplicate["semantic_fingerprint"]
    assert duplicate["semantic_schema_version"] == 3
    assert duplicate["equivalent_to_version_id"] == first_version
    assert duplicate_response in json.dumps(duplicate["source_payload"], ensure_ascii=False)
    assert duplicate_steps == 0
    assert generation_jobs == 1
    assert summaries == 1

    await pool.close()


@pytest.mark.asyncio
async def test_equivalent_response_repairs_nonpublishable_current_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE public_summary_requests, tenant_judit_requests, jobs,
                         process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )

        code = "0000000-00.2026.8.21.0200"
        first_request = f"req-first-{uuid4()}"
        _, first_version = await _stage(
            pool,
            _lawsuit_event(
                request_id=first_request,
                response_id=f"resp-first-{uuid4()}",
                callback_id=f"cb-first-{uuid4()}",
                code=code,
            ),
        )
        first_result = await finalize_judit_request_task({"request_id": first_request})
        assert first_result["promoted"] is True

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                """
                SELECT id, code, class_name, court, header, parties
                FROM processes
                WHERE code = $1
                """,
                code,
            )
            assert process is not None
            header = process["header"]
            parties = process["parties"]
            if isinstance(header, str):
                header = json.loads(header)
            if isinstance(parties, str):
                parties = json.loads(parties)
            claim_context = {
                "code": process["code"],
                "class_name": process["class_name"],
                "court": process["court"],
                "header": header,
                "parties": parties,
                "_process_evidence_ref": process_evidence_ref(first_version),
                "_selected_sources": [],
                "_attachment_sources": [],
            }
            claim_payload = {
                "synthesis": "Resumo sintético",
                "timeline": [],
                "current_status": "Situação atual registrada.",
                "attention": ["Nenhum ponto adicional de atenção."],
                "decisions": [],
                "deadlines": [],
                "related_processes": [],
                "attachments": [],
            }
            claim_payload["claims"] = build_material_claims(
                claim_payload,
                evidence_refs=[claim_context["_process_evidence_ref"]],
            )
            claims, claim_errors = validate_claim_evidence(
                claim_payload,
                claim_context,
            )
            assert claim_errors == []
            assert await _persist_summary(
                conn,
                process_id=process["id"],
                version_id=first_version,
                text="# resumo sintético",
                validation={"passed": True, "errors": []},
                generation_ms=1,
                structured_output=structured_summary_document(
                    claim_payload,
                    claim_context,
                ),
                claims=claims,
                evidence_sources=evidence_catalog(claim_context),
            )

            summary_id = await conn.fetchval(
                """
                SELECT id FROM process_summaries
                WHERE process_id=$1 AND version_id=$2
                """,
                process["id"],
                first_version,
            )
            assert summary_id is not None
            await conn.execute(
                """
                DELETE FROM process_summary_claim_sources
                WHERE summary_id=$1
                """,
                summary_id,
            )

        duplicate_request = f"req-duplicate-{uuid4()}"
        _, duplicate_version = await _stage(
            pool,
            _lawsuit_event(
                request_id=duplicate_request,
                response_id=f"resp-duplicate-{uuid4()}",
                callback_id=f"cb-duplicate-{uuid4()}",
                code=code,
            ),
        )

        tenant_id = uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'repair tenant')",
                tenant_id,
            )
            public_request, _ = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=code,
                idempotency_key="repair-equivalent",
                fingerprint=request_fingerprint({"cnj": code}),
            )
            tenant_request_id = await conn.fetchval(
                """
                INSERT INTO tenant_judit_requests (
                    tenant_id, process_code, judit_request_id, status, attempt_number
                )
                VALUES ($1, $2, $3, 'processing', 1)
                RETURNING id
                """,
                tenant_id,
                code,
                duplicate_request,
            )
            await link_summary_request_to_acquisition(
                conn,
                request_id=public_request.id,
                tenant_id=tenant_id,
                process_code=code,
                tenant_judit_request_id=tenant_request_id,
            )
            await transition_summary_request(
                conn,
                request_id=public_request.id,
                status="fetching",
            )

        result = await finalize_judit_request_task({"request_id": duplicate_request})

        assert result["status"] == "finalized_unchanged"
        assert result["promoted"] is False
        assert result["equivalent_to_version_id"] == str(first_version)
        assert result["summary_enqueued"] is True
        assert duplicate_version != first_version

        async with pool.acquire() as conn:
            request_state = await conn.fetchrow(
                """
                SELECT status::text, process_id, version_id, summary_id, error_code
                FROM public_summary_requests
                WHERE id=$1
                """,
                public_request.id,
            )
            repair_job = await conn.fetchrow(
                """
                SELECT task_name, payload, status::text
                FROM jobs
                WHERE idempotency_key=$1
                """,
                f"summary-repair:{first_version}:{duplicate_request}",
            )

        assert request_state["status"] == "indexing"
        assert request_state["process_id"] == process["id"]
        assert request_state["version_id"] == first_version
        assert request_state["summary_id"] is None
        assert request_state["error_code"] is None
        assert repair_job is not None
        assert repair_job["task_name"] == "generate_process_summary"
        repair_payload = repair_job["payload"]
        if isinstance(repair_payload, str):
            repair_payload = json.loads(repair_payload)
        assert repair_payload["process_id"] == str(process["id"])
        assert repair_payload["version_id"] == str(first_version)
        assert repair_payload["judit_request_id"] == duplicate_request
        assert repair_job["status"] == "pending"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_restricted_summary_reuse_fails_after_process_becomes_public() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        process_id = uuid4()
        version_id = uuid4()
        code = "0000000-00.2026.8.21.0199"
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE jobs, process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                """
                INSERT INTO processes (
                    id, code, secrecy_level, current_version_id
                ) VALUES ($1, $2, 1, NULL)
                """,
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (
                    id, process_id, source_request_id, finalized
                ) VALUES ($1, $2, $3, TRUE)
                """,
                version_id,
                process_id,
                f"restricted-{version_id}",
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )
            await conn.execute(
                """
                INSERT INTO process_summaries (
                    process_id, version_id, markdown, validation, model,
                    prompt_version, generation_ms, structured_output
                ) VALUES ($1, $2, '# resumo restrito',
                          '{"passed": true, "errors": []}'::jsonb, $3, $4, 1,
                          $5::jsonb)
                """,
                process_id,
                version_id,
                RESTRICTED_MODEL,
                RESTRICTED_PROMPT_VERSION,
                json.dumps(_restricted_structured_output(code)),
            )

            assert await _complete_from_current_summary(
                conn,
                request_id=f"secret-{uuid4()}",
                process_id=process_id,
            ) is True

            await conn.execute(
                "UPDATE processes SET secrecy_level=0 WHERE id=$1",
                process_id,
            )

            assert await _complete_from_current_summary(
                conn,
                request_id=f"public-{uuid4()}",
                process_id=process_id,
            ) is False
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_public_lifecycle_completion_uses_publication_gate() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        tenant_id = uuid4()
        process_id = uuid4()
        version_id = uuid4()
        code = "0000000-00.2026.8.21.0198"
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE public_summary_requests, jobs, process_summaries,
                         process_steps, tenant_processes, access_log,
                         process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, 'publication-gate')",
                tenant_id,
            )
            await conn.execute(
                """
                INSERT INTO processes (
                    id, code, secrecy_level, current_version_id
                ) VALUES ($1, $2, 0, NULL)
                """,
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (
                    id, process_id, source_request_id, finalized
                ) VALUES ($1, $2, $3, TRUE)
                """,
                version_id,
                process_id,
                f"publication-{version_id}",
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )
            summary_id = await conn.fetchval(
                """
                INSERT INTO process_summaries (
                    process_id, version_id, markdown, validation, model,
                    prompt_version, generation_ms
                ) VALUES ($1, $2, '# incomplete public',
                          '{"passed": true, "errors": []}'::jsonb,
                          'fake-offline', 'test-v1', 1)
                RETURNING id
                """,
                process_id,
                version_id,
            )

            async def new_request(key: str):
                request, _ = await create_or_get_summary_request(
                    conn,
                    tenant_id=tenant_id,
                    process_code=code,
                    idempotency_key=key,
                    fingerprint=request_fingerprint({"cnj": code, "key": key}),
                )
                return await transition_summary_request(
                    conn,
                    request_id=request.id,
                    status="generating",
                    process_id=process_id,
                    version_id=version_id,
                )

            public_request = await new_request("public-incomplete")
            await reconcile_generation_result(
                conn,
                payload={
                    "process_id": str(process_id),
                    "version_id": str(version_id),
                },
                result={"validation": {"passed": True}, "persisted": True},
            )
            public_state = await conn.fetchrow(
                """
                SELECT status, summary_id, error_code
                FROM public_summary_requests
                WHERE id=$1
                """,
                public_request.id,
            )
            assert public_state["status"] == "validation_failed"
            assert public_state["summary_id"] is None
            assert public_state["error_code"] == "validation_failed"

            await conn.execute(
                """
                UPDATE processes SET secrecy_level=1 WHERE id=$1
                """,
                process_id,
            )
            await conn.execute(
                """
                UPDATE process_summaries
                SET model=$2, prompt_version=$3, structured_output=$4::jsonb
                WHERE id=$1
                """,
                summary_id,
                RESTRICTED_MODEL,
                RESTRICTED_PROMPT_VERSION,
                json.dumps(_restricted_structured_output(code)),
            )
            restricted_request = await new_request("restricted-valid")
            await reconcile_generation_result(
                conn,
                payload={
                    "process_id": str(process_id),
                    "version_id": str(version_id),
                },
                result={"validation": {"passed": True}, "persisted": True},
            )
            restricted_state = await conn.fetchrow(
                """
                SELECT status, summary_id, error_code
                FROM public_summary_requests
                WHERE id=$1
                """,
                restricted_request.id,
            )
            assert restricted_state["status"] == "completed"
            assert restricted_state["summary_id"] == summary_id
            assert restricted_state["error_code"] is None

            await conn.execute(
                "UPDATE processes SET secrecy_level=0 WHERE id=$1",
                process_id,
            )
            reopened_request = await new_request("restricted-after-public")
            await reconcile_generation_result(
                conn,
                payload={
                    "process_id": str(process_id),
                    "version_id": str(version_id),
                },
                result={"validation": {"passed": True}, "reused": True},
            )
            reopened_state = await conn.fetchrow(
                """
                SELECT status, summary_id, error_code
                FROM public_summary_requests
                WHERE id=$1
                """,
                reopened_request.id,
            )
            assert reopened_state["status"] == "validation_failed"
            assert reopened_state["summary_id"] is None
            assert reopened_state["error_code"] == "validation_failed"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_new_movement_or_relevant_metadata_creates_new_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, process_summaries, process_steps, tenant_processes,
                     access_log, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    code = "0000000-00.2026.8.21.0137"
    request_1 = f"req-1-{uuid4()}"
    _, version_1 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_1,
            response_id=f"resp-1-{uuid4()}",
            callback_id=f"cb-1-{uuid4()}",
            code=code,
        ),
    )
    assert (await finalize_judit_request_task({"request_id": request_1}))["promoted"] is True

    request_2 = f"req-2-{uuid4()}"
    _, version_2 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_2,
            response_id=f"resp-2-{uuid4()}",
            callback_id=f"cb-2-{uuid4()}",
            code=code,
            extra_step=True,
        ),
    )
    result_2 = await finalize_judit_request_task({"request_id": request_2})
    assert result_2["status"] == "finalized"
    assert result_2["promoted"] is True
    assert result_2["summary_enqueued"] is True

    request_3 = f"req-3-{uuid4()}"
    _, version_3 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_3,
            response_id=f"resp-3-{uuid4()}",
            callback_id=f"cb-3-{uuid4()}",
            code=code,
            class_name="Execução Fiscal",
            extra_step=True,
        ),
    )
    result_3 = await finalize_judit_request_task({"request_id": request_3})
    assert result_3["status"] == "finalized"
    assert result_3["promoted"] is True
    assert result_3["summary_enqueued"] is True

    async with pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE code = $1", code
        )
        generation_jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name = 'generate_process_summary'"
        )
        step_counts = [
            await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE version_id = $1", version
            )
            for version in (version_1, version_2, version_3)
        ]

    assert current_version == version_3
    assert generation_jobs == 3
    assert step_counts == [1, 2, 2]

    await pool.close()



@pytest.mark.asyncio
async def test_new_attachment_manifest_promotes_version_and_persists_pending_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, process_summary_attachment_sources, attachment_chunks,
                     process_attachments, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    code = "0000000-00.2026.8.21.0138"
    request_1 = f"req-1-{uuid4()}"
    _, version_1 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_1,
            response_id=f"resp-1-{uuid4()}",
            callback_id=f"cb-1-{uuid4()}",
            code=code,
        ),
    )
    assert (await finalize_judit_request_task({"request_id": request_1}))["promoted"] is True

    request_2 = f"req-2-{uuid4()}"
    _, version_2 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_2,
            response_id=f"resp-2-{uuid4()}",
            callback_id=f"cb-2-{uuid4()}",
            code=code,
            attachments=[
                {
                    "attachment_id": "att-decisao-1",
                    "attachment_date": "2026-09-17T12:00:00Z",
                    "attachment_name": "DECISAO 1.pdf",
                    "status": "done",
                    "signed_url": "https://must-not-persist.invalid/attachment",
                }
            ],
        ),
    )
    result_2 = await finalize_judit_request_task({"request_id": request_2})

    assert result_2["status"] == "finalized"
    assert result_2["promoted"] is True
    assert result_2["summary_enqueued"] is True

    async with pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE code=$1", code
        )
        attachment = await conn.fetchrow(
            """
            SELECT source_attachment_id, source_name, source_date, provider_status,
                   status, content_sha256
            FROM process_attachments
            WHERE version_id=$1
            """,
            version_2,
        )
        schema_version = await conn.fetchval(
            "SELECT semantic_schema_version FROM process_versions WHERE id=$1",
            version_2,
        )

    assert current_version == version_2
    assert current_version != version_1
    assert attachment["source_attachment_id"] == "att-decisao-1"
    assert attachment["source_name"] == "DECISAO 1.pdf"
    assert attachment["source_date"].isoformat() == "2026-09-17T12:00:00+00:00"
    assert attachment["provider_status"] == "done"
    assert attachment["status"] == "pending"
    assert attachment["content_sha256"] is None
    assert schema_version == 3

    await pool.close()
