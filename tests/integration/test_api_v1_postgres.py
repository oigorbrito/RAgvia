from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app.api_key_auth import api_key_hash_and_fingerprint
from app.db import create_pool
from app.migrations import migrate
from app.summary_output import structured_summary_document
from app.summary_policy import RESTRICTED_MODEL, RESTRICTED_PROMPT_VERSION
from app.public_lifecycle import (
    create_or_get_summary_request,
    request_fingerprint,
    transition_summary_request,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _insert_key(conn, *, tenant_id, token: str, scopes: tuple[str, ...]):
    key_hash, fingerprint = api_key_hash_and_fingerprint(token)
    key_id = await conn.fetchval(
        """
        INSERT INTO api_keys (
            tenant_id, name, key_hash, fingerprint, environment,
            allow_portfolio, rate_limit_per_minute
        )
        VALUES ($1, 'v1-test', $2, $3, 'test', FALSE, 200)
        RETURNING id
        """,
        tenant_id,
        key_hash,
        fingerprint,
    )
    for code in scopes:
        await conn.execute(
            "INSERT INTO api_key_cnj_scopes (api_key_id, process_code) VALUES ($1,$2)",
            key_id,
            code,
        )
    return key_id


async def _reset(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE api_key_rate_limits, api_key_cnj_scopes, api_keys,
                     public_summary_requests, jobs, judit_deliveries,
                     judit_request_completions, process_summaries, process_steps,
                     tenant_processes, tenant_judit_requests, access_log,
                     process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )


@pytest.mark.asyncio
async def test_v1_create_summary_rejects_non_standard_json_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("RPY_API_KEY_ENVIRONMENT", "test")
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    app.state.pool = pool
    app.state.bearer_tokens = {}

    code = "0000000-00.2026.8.21.0399"
    token = "sk_test_strict_json_00000001"
    try:
        await _reset(pool)
        async with pool.acquire() as conn:
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('Strict JSON tenant') RETURNING id"
            )
            await _insert_key(conn, tenant_id=tenant_id, token=token, scopes=(code,))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/resumos",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "idem-non-standard-json",
                    "Content-Type": "application/json",
                },
                content=(
                    '{"cnj":"0000000-00.2026.8.21.0399",'
                    '"format":"json","unused":{"score":NaN}}'
                ),
            )

        if response.status_code != 400:
            raise AssertionError(
                f"expected strict JSON rejection with 400, got {response.status_code}"
            )
        if response.json().get("detail") != "request body must contain strict JSON":
            raise AssertionError(f"unexpected strict JSON error: {response.json()!r}")
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_v1_idempotency_states_authorization_and_sanitized_sources(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("RPY_API_KEY_ENVIRONMENT", "test")
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=8)
    app.state.pool = pool
    app.state.bearer_tokens = {}

    code = "0000000-00.2026.8.21.0401"
    other_code = "0000000-00.2026.8.21.0402"
    token_a = "sk_test_public_v1_tenant_a_00000001"
    token_b = "sk_test_public_v1_tenant_b_00000002"
    raw_sentinel = "RAW-SOURCE-PAYLOAD-MUST-NOT-LEAK"
    claim_excerpt_sentinel = "CLAIM-EVIDENCE-EXCERPT-MUST-NOT-LEAK"

    try:
        await _reset(pool)
        async with pool.acquire() as conn:
            tenant_a = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('V1 tenant A') RETURNING id"
            )
            tenant_b = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('V1 tenant B') RETURNING id"
            )
            await _insert_key(conn, tenant_id=tenant_a, token=token_a, scopes=(code, other_code))
            await _insert_key(conn, tenant_id=tenant_b, token=token_b, scopes=(code,))

        transport = httpx.ASGITransport(app=app)
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "Idempotency-Key": "idem-v1-create",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/v1/resumos",
                headers=headers_a,
                json={"cnj": code, "mode": "default"},
            )
            assert created.status_code == 202
            assert created.headers["X-RateLimit-Remaining"] == "199"
            created_body = created.json()
            assert created_body["status"] == "queued"
            assert created_body["cnj"] == code
            assert created_body["format"] == "jsx"
            job_id = created_body["job_id"]
            assert created_body["poll_url"] == f"/v1/resumos/{job_id}"

            replay = await client.post(
                "/v1/resumos",
                headers=headers_a,
                json={"cnj": code, "mode": "default"},
            )
            assert replay.status_code == 202
            assert replay.json()["job_id"] == job_id

            conflict = await client.post(
                "/v1/resumos",
                headers=headers_a,
                json={"cnj": code, "mode": "different"},
            )
            assert conflict.status_code == 409

            json_headers = {
                "Authorization": f"Bearer {token_a}",
                "Idempotency-Key": "idem-v1-create-json",
            }
            json_created = await client.post(
                "/v1/resumos",
                headers=json_headers,
                json={"cnj": code, "mode": "default", "format": "json"},
            )
            assert json_created.status_code == 202
            assert json_created.json()["format"] == "json"
            assert json_created.json()["job_id"] != job_id

            invalid_format = await client.post(
                "/v1/resumos",
                headers={
                    "Authorization": f"Bearer {token_a}",
                    "Idempotency-Key": "idem-invalid-format",
                },
                json={"cnj": code, "format": "xml"},
            )
            assert invalid_format.status_code == 400

        async with pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM public_summary_requests WHERE tenant_id=$1 AND process_code=$2",
                tenant_a,
                code,
            ) == 2
            assert await conn.fetchval(
                "SELECT count(*) FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
                tenant_a,
                code,
            ) == 1
            assert await conn.fetchval(
                """
                SELECT count(*) FROM jobs
                WHERE task_name='request_judit_process'
                  AND payload->>'tenant_request_id' IN (
                      SELECT id::text FROM tenant_judit_requests
                      WHERE tenant_id=$1 AND process_code=$2
                  )
                """,
                tenant_a,
                code,
            ) == 1

            process_id = await conn.fetchval(
                """
                INSERT INTO processes (
                    code, court, class_name, secrecy_level, header
                )
                VALUES ($1, 'TJRS', 'Procedimento Comum', 0, '{}'::jsonb)
                RETURNING id
                """,
                other_code,
            )
            version_id = await conn.fetchval(
                """
                INSERT INTO process_versions (
                    process_id, source_request_id, source_cached_response,
                    source_payload, finalized, finalized_at
                )
                VALUES ($1, 'source-v1', FALSE, $2::jsonb, TRUE, NOW())
                RETURNING id
                """,
                process_id,
                json.dumps({"sentinel": raw_sentinel, "private": "raw-only"}),
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2, updated_at=NOW() WHERE id=$1",
                process_id,
                version_id,
            )
            await conn.execute(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1,$2)",
                tenant_a,
                process_id,
            )
            await conn.executemany(
                """
                INSERT INTO process_attachments (
                    process_id, version_id, source_attachment_id, status
                ) VALUES ($1, $2, $3, $4)
                """,
                [
                    (process_id, version_id, "doc-ready", "ready"),
                    (process_id, version_id, "doc-pending", "pending"),
                    (process_id, version_id, "doc-unavailable", "unavailable"),
                    (process_id, version_id, "doc-corrupt", "corrupt"),
                    (process_id, version_id, "doc-unreadable", "unreadable"),
                ],
            )
            await conn.execute(
                """
                INSERT INTO process_steps (
                    process_id, version_id, step_number, occurred_at, title, text, metadata
                )
                VALUES ($1,$2,1,$3,'SENTENÇA','Texto normalizado seguro',
                        '{"source_step_number": 77}'::jsonb)
                """,
                process_id,
                version_id,
                datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            )
            summary_id = await conn.fetchval(
                """
                INSERT INTO process_summaries (
                    process_id, version_id, markdown, structured_output, validation,
                    model, prompt_version, generation_ms
                )
                VALUES ($1,$2,'# Resumo válido',$3::jsonb,
                        '{"passed": true, "errors": []}'::jsonb,
                        'fake-offline','test-v1',12)
                RETURNING id
                """,
                process_id,
                version_id,
                json.dumps(
                    {
                        "schema_version": 2,
                        "process": {
                            "cnj": other_code,
                            "class_name": "Procedimento Comum",
                            "court": "TJRS",
                            "header": {},
                            "parties": [],
                        },
                        "summary": {
                            "synthesis": "Resumo válido",
                            "timeline": [],
                            "current_status": "Situação registrada.",
                            "attention": ["Nenhuma divergência objetiva identificada."],
                            "decisions": [],
                            "deadlines": [],
                            "related_processes": [],
                            "attachments": [],
                            "claims": [
                                {
                                    "claim_id": "synthesis",
                                    "text": "Resumo válido",
                                    "evidence_refs": [f"p-{version_id.hex}"],
                                },
                                {
                                    "claim_id": "current_status",
                                    "text": "Situação registrada.",
                                    "evidence_refs": [f"p-{version_id.hex}"],
                                },
                                {
                                    "claim_id": "attention:0",
                                    "text": "Nenhuma divergência objetiva identificada.",
                                    "evidence_refs": [f"p-{version_id.hex}"],
                                },
                            ],
                        },
                    }
                ),
            )
            for claim_id, claim_class, claim_text in (
                ("synthesis", "synthesis", "Resumo válido"),
                ("current_status", "current_status", "Situação registrada."),
                (
                    "attention:0",
                    "attention",
                    "Nenhuma divergência objetiva identificada.",
                ),
            ):
                claim_row_id = await conn.fetchval(
                    """
                    INSERT INTO process_summary_claims (
                        summary_id, process_id, version_id,
                        claim_id, claim_class, claim_text
                    ) VALUES ($1,$2,$3,$4,$5,$6)
                    RETURNING id
                    """,
                    summary_id,
                    process_id,
                    version_id,
                    claim_id,
                    claim_class,
                    claim_text,
                )
                await conn.execute(
                    """
                    INSERT INTO process_summary_claim_sources (
                        claim_row_id, summary_id, process_id, version_id,
                        evidence_ref, source_kind, source_order
                    ) VALUES ($1,$2,$3,$4,$5,'process',0)
                    """,
                    claim_row_id,
                    summary_id,
                    process_id,
                    version_id,
                    f"p-{version_id.hex}",
                )

            await conn.execute(
                """
                UPDATE process_summary_claim_sources
                SET evidence_excerpt_sha256=$2
                WHERE summary_id=$1 AND source_order=0
                """,
                summary_id,
                "a" * 64,
            )
            await conn.execute(
                """
                INSERT INTO process_summary_claim_evidence_excerpts (
                    claim_row_id, evidence_ref, evidence_excerpt
                )
                SELECT claim_row_id, evidence_ref, $2
                FROM process_summary_claim_sources
                WHERE summary_id=$1 AND source_order=0
                """,
                summary_id,
                claim_excerpt_sentinel,
            )

            completed_request, _ = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_a,
                process_code=other_code,
                idempotency_key="idem-completed",
                fingerprint=request_fingerprint({"cnj": other_code}),
            )
            await transition_summary_request(
                conn,
                request_id=completed_request.id,
                status="completed",
                process_id=process_id,
                version_id=version_id,
                summary_id=summary_id,
                source_updated_at=datetime.now(timezone.utc),
                flags={"reused_existing_summary": False},
            )
            json_request, _ = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_a,
                process_code=other_code,
                idempotency_key="idem-completed-json",
                fingerprint=request_fingerprint({"cnj": other_code, "format": "json"}),
                response_format="json",
            )
            await transition_summary_request(
                conn,
                request_id=json_request.id,
                status="completed",
                process_id=process_id,
                version_id=version_id,
                summary_id=summary_id,
                source_updated_at=datetime.now(timezone.utc),
            )

            state_ids: dict[str, str] = {}
            for index, status in enumerate(
                (
                    "queued",
                    "fetching",
                    "indexing",
                    "generating",
                    "validating",
                    "failed",
                    "source_unavailable",
                    "secrecy_blocked",
                    "validation_failed",
                ),
                start=1,
            ):
                state_request, _ = await create_or_get_summary_request(
                    conn,
                    tenant_id=tenant_a,
                    process_code=other_code,
                    idempotency_key=f"idem-state-{index}",
                    fingerprint=request_fingerprint({"cnj": other_code, "status": status}),
                )
                if status != "queued":
                    await transition_summary_request(
                        conn,
                        request_id=state_request.id,
                        status=status,
                        error_code=status if status in {
                            "failed",
                            "source_unavailable",
                            "secrecy_blocked",
                            "validation_failed",
                        } else None,
                    )
                state_ids[status] = str(state_request.id)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            auth_a = {"Authorization": f"Bearer {token_a}"}
            auth_b = {"Authorization": f"Bearer {token_b}"}

            cross_tenant = await client.get(f"/v1/resumos/{job_id}", headers=auth_b)
            assert cross_tenant.status_code == 404

            for expected_status, state_job_id in state_ids.items():
                state_response = await client.get(
                    f"/v1/resumos/{state_job_id}", headers=auth_a
                )
                assert state_response.status_code == 200
                assert state_response.json()["status"] == expected_status

            completed_job = await client.get(
                f"/v1/resumos/{completed_request.id}", headers=auth_a
            )
            assert completed_job.status_code == 200
            completed_body = completed_job.json()
            assert completed_body["status"] == "completed"
            assert completed_body["format"] == "jsx"
            assert completed_body["iaSummary"] == "# Resumo válido"
            assert completed_body["validation"]["passed"] is True
            assert completed_body["flags"]["reused_existing_summary"] is False
            assert completed_body["flags"]["attachments"] == {
                "total": 5,
                "status_counts": {
                    "pending": 1,
                    "ready": 1,
                    "unavailable": 1,
                    "corrupt": 1,
                    "unreadable": 1,
                },
                "processing_complete": False,
                "degraded": True,
            }
            assert completed_body["usage"] == {
                "model": "fake-offline",
                "prompt_version": "test-v1",
                "generation_ms": 12,
            }
            assert any(source["kind"] == "movement" for source in completed_body["sources"])
            rendered_job = json.dumps(completed_body, ensure_ascii=False)
            assert raw_sentinel not in rendered_job
            assert "source_payload" not in rendered_job

            json_job = await client.get(
                f"/v1/resumos/{json_request.id}", headers=auth_a
            )
            assert json_job.status_code == 200
            assert json_job.json()["format"] == "json"
            assert json_job.json()["iaSummary"]["schema_version"] == 2
            assert json_job.json()["iaSummary"]["process"]["cnj"] == other_code
            assert json_job.json()["iaSummary"]["summary"]["synthesis"] == "Resumo válido"
            json_claims = json_job.json()["iaSummary"]["summary"]["claims"]
            synthesis_claim = next(
                item for item in json_claims if item["claim_id"] == "synthesis"
            )
            assert synthesis_claim["verification"]["status"] == "not_evaluated"
            assert all(
                "evidence_excerpt" not in source
                for source in synthesis_claim["verification"]["sources"]
            )
            assert claim_excerpt_sentinel not in json.dumps(
                json_job.json(),
                ensure_ascii=False,
            )

            summary = await client.get(
                f"/v1/processos/{other_code}/resumo", headers=auth_a
            )
            assert summary.status_code == 200
            assert summary.json()["iaSummary"] == "# Resumo válido"
            assert summary.json()["validation"]["passed"] is True
            assert summary.json()["flags"]["attachments"]["degraded"] is True
            assert summary.json()["flags"]["attachments"]["status_counts"]["pending"] == 1

            json_summary = await client.get(
                f"/v1/processos/{other_code}/resumo?format=json", headers=auth_a
            )
            assert json_summary.status_code == 200
            assert json_summary.json()["format"] == "json"
            assert json_summary.json()["iaSummary"] == json_job.json()["iaSummary"]

            invalid_format = await client.get(
                f"/v1/processos/{other_code}/resumo?format=xml", headers=auth_a
            )
            assert invalid_format.status_code == 400

            sources = await client.get(
                f"/v1/processos/{other_code}/fontes", headers=auth_a
            )
            assert sources.status_code == 200
            source_body = sources.json()
            movement = next(item for item in source_body["sources"] if item["kind"] == "movement")
            assert movement["step_number"] == 1
            assert movement["source_step_number"] == 77
            assert movement["title"] == "SENTENÇA"
            assert source_body["flags"]["attachments"]["degraded"] is True
            assert source_body["flags"]["attachments"]["status_counts"] == {
                "pending": 1,
                "ready": 1,
                "unavailable": 1,
                "corrupt": 1,
                "unreadable": 1,
            }
            rendered_sources = json.dumps(source_body, ensure_ascii=False)
            assert raw_sentinel not in rendered_sources
            assert "source_payload" not in rendered_sources

            async with pool.acquire() as conn:
                original_structured_output = await conn.fetchval(
                    "SELECT structured_output::text FROM process_summaries WHERE id=$1",
                    summary_id,
                )
                await conn.execute(
                    """
                    UPDATE process_summaries
                    SET structured_output = jsonb_set(
                        structured_output,
                        '{process,parties}',
                        '[{"name":"PARTE INJETADA"}]'::jsonb,
                        true
                    )
                    WHERE id=$1
                    """,
                    summary_id,
                )

            tampered_job = await client.get(
                f"/v1/resumos/{completed_request.id}", headers=auth_a
            )
            assert tampered_job.status_code == 200
            assert tampered_job.json()["iaSummary"] is None
            assert tampered_job.json()["claim_evidence"] == []
            assert all(
                "used_for_summary" not in item
                for item in tampered_job.json()["sources"]
            )

            tampered_latest = await client.get(
                f"/v1/processos/{other_code}/resumo?format=json", headers=auth_a
            )
            assert tampered_latest.status_code == 404

            tampered_sources = await client.get(
                f"/v1/processos/{other_code}/fontes", headers=auth_a
            )
            assert tampered_sources.status_code == 200
            assert tampered_sources.json()["claim_evidence"] == []
            assert all(
                "used_for_summary" not in item
                for item in tampered_sources.json()["sources"]
            )

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE process_summaries SET structured_output=$2::jsonb WHERE id=$1",
                    summary_id,
                    original_structured_output,
                )
                await conn.execute(
                    "UPDATE processes SET secrecy_level=1 WHERE id=$1",
                    process_id,
                )

            secret_job = await client.get(
                f"/v1/resumos/{completed_request.id}", headers=auth_a
            )
            assert secret_job.status_code == 200
            assert secret_job.json()["claim_evidence"] == []
            assert secret_job.json()["iaSummary"] is None

            secret_json_job = await client.get(
                f"/v1/resumos/{json_request.id}", headers=auth_a
            )
            assert secret_json_job.status_code == 200
            assert secret_json_job.json()["claim_evidence"] == []
            assert secret_json_job.json()["iaSummary"] is None

            secret_summary = await client.get(
                f"/v1/processos/{other_code}/resumo", headers=auth_a
            )
            assert secret_summary.status_code == 404

            secret_json_summary = await client.get(
                f"/v1/processos/{other_code}/resumo?format=json", headers=auth_a
            )
            assert secret_json_summary.status_code == 404

            secret_sources = await client.get(
                f"/v1/processos/{other_code}/fontes", headers=auth_a
            )
            assert secret_sources.status_code == 200
            assert secret_sources.json()["claim_evidence"] == []
            assert all(
                item["kind"] != "movement"
                for item in secret_sources.json()["sources"]
            )

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE processes SET secrecy_level=0 WHERE id=$1",
                    process_id,
                )
                await conn.execute(
                    """
                    DELETE FROM process_summary_claims
                    WHERE summary_id=$1 AND claim_id='current_status'
                    """,
                    summary_id,
                )

            incomplete_job = await client.get(
                f"/v1/resumos/{completed_request.id}", headers=auth_a
            )
            assert incomplete_job.status_code == 200
            assert incomplete_job.json()["iaSummary"] is None
            assert incomplete_job.json()["claim_evidence"] == []
            assert all(
                "used_for_summary" not in item
                for item in incomplete_job.json()["sources"]
            )

            incomplete_sources = await client.get(
                f"/v1/processos/{other_code}/fontes", headers=auth_a
            )
            assert incomplete_sources.status_code == 200
            assert incomplete_sources.json()["claim_evidence"] == []
            assert all(
                "used_for_summary" not in item
                for item in incomplete_sources.json()["sources"]
            )

            incomplete_latest = await client.get(
                f"/v1/processos/{other_code}/resumo", headers=auth_a
            )
            assert incomplete_latest.status_code == 404

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE processes SET secrecy_level=1 WHERE id=$1",
                    process_id,
                )
                restricted_structured_output = structured_summary_document(
                    {
                        "synthesis": "Os detalhes processuais foram restringidos por sigilo.",
                        "timeline": [],
                        "current_status": (
                            "O contexto público disponível está limitado pelos dados "
                            "permitidos para processo sigiloso."
                        ),
                        "attention": ["Processo com detalhes restringidos por sigilo."],
                        "decisions": [],
                        "deadlines": [],
                        "related_processes": [],
                        "attachments": [],
                    },
                    {
                        "code": other_code,
                        "class_name": "Procedimento Comum",
                        "court": None,
                        "header": {},
                        "parties": [],
                    },
                )
                await conn.execute(
                    """
                    UPDATE process_summaries
                    SET markdown=$2, model=$3, prompt_version=$4,
                        structured_output=$5::jsonb
                    WHERE id=$1
                    """,
                    summary_id,
                    "# Resumo sigiloso local",
                    RESTRICTED_MODEL,
                    RESTRICTED_PROMPT_VERSION,
                    json.dumps(restricted_structured_output),
                )
                await conn.execute(
                    "DELETE FROM process_summary_claims WHERE summary_id=$1",
                    summary_id,
                )

            restricted_job = await client.get(
                f"/v1/resumos/{completed_request.id}", headers=auth_a
            )
            assert restricted_job.status_code == 200
            assert restricted_job.json()["iaSummary"] == "# Resumo sigiloso local"
            assert restricted_job.json()["claim_evidence"] == []

            restricted_latest = await client.get(
                f"/v1/processos/{other_code}/resumo", headers=auth_a
            )
            assert restricted_latest.status_code == 200
            assert restricted_latest.json()["iaSummary"] == "# Resumo sigiloso local"

            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE processes SET secrecy_level=0 WHERE id=$1",
                    process_id,
                )

            public_after_restricted = await client.get(
                f"/v1/resumos/{completed_request.id}", headers=auth_a
            )
            assert public_after_restricted.status_code == 200
            assert public_after_restricted.json()["iaSummary"] is None
            assert public_after_restricted.json()["claim_evidence"] == []
            assert all(
                "used_for_summary" not in item
                for item in public_after_restricted.json()["sources"]
            )

            public_after_restricted_sources = await client.get(
                f"/v1/processos/{other_code}/fontes", headers=auth_a
            )
            assert public_after_restricted_sources.status_code == 200
            assert public_after_restricted_sources.json()["claim_evidence"] == []
            assert all(
                "used_for_summary" not in item
                for item in public_after_restricted_sources.json()["sources"]
            )

            public_after_restricted_latest = await client.get(
                f"/v1/processos/{other_code}/resumo", headers=auth_a
            )
            assert public_after_restricted_latest.status_code == 404

            healthz = await client.get("/healthz")
            readyz = await client.get("/readyz")
            assert healthz.status_code == 200
            assert healthz.json() == {"ok": True}
            assert readyz.status_code == 200
            assert readyz.json() == {"ok": True}

        async with pool.acquire() as conn:
            public_actions = await conn.fetch(
                """
                SELECT action, api_key_fingerprint, metadata
                FROM access_log
                WHERE tenant_id=$1 AND action LIKE 'v1_%'
                ORDER BY id
                """,
                tenant_a,
            )
            assert public_actions
            rendered_audit = json.dumps(
                [dict(row) for row in public_actions], ensure_ascii=False, default=str
            )
            assert token_a not in rendered_audit
            assert raw_sentinel not in rendered_audit
    finally:
        await pool.close()
