from __future__ import annotations

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
from app.migrations import migrate
from app.rag import _persist_summary
from app.summary_output import structured_summary_document

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)


async def _fixture(conn):
    process_id = uuid4()
    version_id = uuid4()
    serial = f"{process_id.int % 100_000_000_000:011d}"
    code = f"{serial[:7]}-00.0000.0.00.{serial[7:]}"
    await conn.execute(
        "INSERT INTO processes (id, code) VALUES ($1, $2)",
        process_id,
        code,
    )
    await conn.execute(
        "INSERT INTO process_versions (id, process_id, source_request_id) VALUES ($1, $2, $3)",
        version_id,
        process_id,
        f"test-{version_id}",
    )
    await conn.execute(
        "UPDATE processes SET current_version_id = $2 WHERE id = $1",
        process_id,
        version_id,
    )
    return process_id, version_id


@pytest.mark.asyncio
async def test_valid_summary_cannot_be_overwritten_by_invalid_duplicate() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id = await _fixture(conn)
            first = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="valid winner",
                validation={"passed": True, "errors": []},
                generation_ms=100,
            )
            stale = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="invalid stale worker",
                validation={"passed": False, "errors": ["bad"]},
                generation_ms=200,
            )
            stored = await conn.fetchrow(
                "SELECT markdown, validation, generation_ms FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )

        assert first is True
        assert stale is False
        assert stored is not None
        assert stored["markdown"] == "valid winner"
        assert stored["validation"]["passed"] is True
        assert int(stored["generation_ms"]) == 100
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_invalid_summary_can_be_upgraded_by_valid_retry() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id = await _fixture(conn)
            invalid = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="invalid first",
                validation={"passed": False, "errors": ["bad"]},
                generation_ms=300,
            )
            upgraded = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="valid replacement",
                validation={"passed": True, "errors": []},
                generation_ms=400,
            )
            stored = await conn.fetchrow(
                "SELECT markdown, validation, generation_ms FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )

        assert invalid is True
        assert upgraded is True
        assert stored is not None
        assert stored["markdown"] == "valid replacement"
        assert stored["validation"]["passed"] is True
        assert int(stored["generation_ms"]) == 400
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_same_revision_valid_duplicate_keeps_first_accepted_summary() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id = await _fixture(conn)
            first = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="first accepted",
                validation={"passed": True, "errors": []},
                generation_ms=500,
            )
            duplicate = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="second accepted but duplicate",
                validation={"passed": True, "errors": []},
                generation_ms=600,
            )
            stored = await conn.fetchrow(
                "SELECT markdown, generation_ms FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )

        assert first is True
        assert duplicate is False
        assert stored is not None
        assert stored["markdown"] == "first accepted"
        assert int(stored["generation_ms"]) == 500
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_new_prompt_revision_may_replace_valid_summary_only_with_valid_output() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id = await _fixture(conn)
            code = await conn.fetchval(
                "SELECT code FROM processes WHERE id=$1",
                process_id,
            )
            context = {
                "code": code,
                "class_name": None,
                "court": None,
                "header": {},
                "parties": [],
                "_process_evidence_ref": process_evidence_ref(version_id),
                "_selected_sources": [],
                "_attachment_sources": [],
            }
            payload = {
                "synthesis": "Resumo válido.",
                "timeline": [],
                "current_status": "Situação atual.",
                "attention": ["Nenhuma divergência objetiva identificada."],
                "decisions": [],
                "deadlines": [],
                "related_processes": [],
                "attachments": [],
            }
            payload["claims"] = build_material_claims(
                payload,
                evidence_refs=[context["_process_evidence_ref"]],
            )
            claims, errors = validate_claim_evidence(payload, context)
            assert errors == []
            structured_output = structured_summary_document(payload, context)
            catalog = evidence_catalog(context)

            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="v2 valid",
                validation={"passed": True, "errors": []},
                generation_ms=700,
                prompt_version="process-summary-v2",
                structured_output=structured_output,
                claims=claims,
                evidence_sources=catalog,
            )
            rejected = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="v3 invalid",
                validation={"passed": False, "errors": ["bad"]},
                generation_ms=800,
                prompt_version="process-summary-v3",
            )
            upgraded = await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="v3 valid",
                validation={"passed": True, "errors": []},
                generation_ms=900,
                prompt_version="process-summary-v3",
                structured_output=structured_output,
                claims=claims,
                evidence_sources=catalog,
            )
            stored = await conn.fetchrow(
                "SELECT markdown, validation, prompt_version, generation_ms FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )

        assert rejected is False
        assert upgraded is True
        assert stored is not None
        assert stored["markdown"] == "v3 valid"
        assert stored["validation"]["passed"] is True
        assert stored["prompt_version"] == "process-summary-v3"
        assert int(stored["generation_ms"]) == 900
    finally:
        await pool.close()
