from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app.api_v1 import _job_payload
from app.claim_evidence import (
    build_material_claims,
    evidence_catalog,
    process_evidence_ref,
    validate_claim_evidence,
)
from app.db import create_pool
from app.migrations import migrate
from app.public_lifecycle import (
    create_or_get_summary_request,
    request_fingerprint,
    transition_summary_request,
)
from app.rag import _persist_summary
from app.summary_output import structured_summary_document

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_generation_telemetry_is_persisted_and_exposed() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    code = "0000000-00.2026.8.21.0610"
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE public_summary_requests, process_summaries, process_steps,
                         tenant_processes, process_versions, processes, tenants
                RESTART IDENTITY CASCADE
                """
            )
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name) VALUES ('generation telemetry') RETURNING id"
            )
            process_id = await conn.fetchval(
                """
                INSERT INTO processes (code, secrecy_level, header)
                VALUES ($1, 0, '{}'::jsonb)
                RETURNING id
                """,
                code,
            )
            version_id = await conn.fetchval(
                """
                INSERT INTO process_versions (
                    process_id, source_request_id, source_payload, finalized, finalized_at
                ) VALUES ($1, 'telemetry-source', '{}'::jsonb, TRUE, NOW())
                RETURNING id
                """,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )
            await conn.execute(
                "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1,$2)",
                tenant_id,
                process_id,
            )

            claim_context = {
                "code": code,
                "class_name": None,
                "court": None,
                "header": {},
                "parties": [],
                "_process_evidence_ref": process_evidence_ref(version_id),
                "_selected_sources": [],
                "_attachment_sources": [],
            }
            summary_payload = {
                "synthesis": "Resumo válido",
                "timeline": [],
                "current_status": "Situação registrada.",
                "attention": ["Nenhuma divergência objetiva identificada."],
                "decisions": [],
                "deadlines": [],
                "related_processes": [],
                "attachments": [],
            }
            summary_payload["claims"] = build_material_claims(
                summary_payload,
                evidence_refs=[claim_context["_process_evidence_ref"]],
            )
            claims, claim_errors = validate_claim_evidence(
                summary_payload, claim_context
            )
            assert claim_errors == []
            structured_output = structured_summary_document(
                summary_payload, claim_context
            )

            persisted = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="# Resumo válido",
                validation={"passed": True, "errors": []},
                generation_ms=321,
                model="claude-opus-5",
                prompt_version="process-summary-v2",
                usage={
                    "input_tokens": 1200,
                    "output_tokens": 300,
                    "cache_creation_input_tokens": 1000,
                    "cache_read_input_tokens": 200,
                },
                cache_hit=True,
                cost_usd=0.01234567,
                structured_output=structured_output,
                claims=claims,
                evidence_sources=evidence_catalog(claim_context),
            )
            assert persisted is True
            summary = await conn.fetchrow(
                """
                SELECT id, usage, cache_hit, cost_usd
                FROM process_summaries
                WHERE process_id=$1 AND version_id=$2
                """,
                process_id,
                version_id,
            )
            assert summary is not None
            assert summary["cache_hit"] is True
            assert float(summary["cost_usd"]) == pytest.approx(0.01234567)

            public_request, _ = await create_or_get_summary_request(
                conn,
                tenant_id=tenant_id,
                process_code=code,
                idempotency_key="telemetry-job",
                fingerprint=request_fingerprint({"cnj": code}),
            )
            await transition_summary_request(
                conn,
                request_id=public_request.id,
                status="completed",
                process_id=process_id,
                version_id=version_id,
                summary_id=summary["id"],
                source_updated_at=datetime.now(timezone.utc),
            )

            loaded = await _job_payload(
                conn,
                tenant_id=tenant_id,
                job_id=public_request.id,
            )
            assert loaded is not None
            _, payload = loaded
            assert payload["usage"] == {
                "model": "claude-opus-5",
                "prompt_version": "process-summary-v2",
                "generation_ms": 321,
                "input_tokens": 1200,
                "output_tokens": 300,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 200,
                "cache_hit": True,
                "cost_usd": pytest.approx(0.01234567),
            }
            assert payload["validation"] == {"passed": True, "errors": []}
            assert payload["iaSummary"] == "# Resumo válido"
    finally:
        await pool.close()
