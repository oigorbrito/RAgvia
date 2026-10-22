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
from app.rag import _persist_summary, generate_summary
from app.summary_output import structured_summary_document
from app.summary_policy import RESTRICTED_MODEL, RESTRICTED_PROMPT_VERSION

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
    return process_id, version_id, code



def _claim_material(code: str, version_id):
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
        "synthesis": "Resumo válido sem dados sensíveis.",
        "timeline": [],
        "current_status": "Situação atual registrada.",
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
    return context, payload, claims


@pytest.mark.asyncio
async def test_existing_valid_summary_skips_context_retrieval_and_providers(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, code = await _fixture(conn)
            claim_context, payload, claims = _claim_material(code, version_id)
            legacy_structured_output = structured_summary_document(
                payload, claim_context
            )
            legacy_structured_output["schema_version"] = 2
            legacy_structured_output["process"].pop("representatives")
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="accepted summary",
                validation={"passed": True, "errors": []},
                generation_ms=123,
                structured_output=legacy_structured_output,
                claims=claims,
                evidence_sources=evidence_catalog(claim_context),
            )

        async def fail_context(*args, **kwargs):
            raise AssertionError("context/retrieval pipeline must not run for an accepted summary")

        def fail_provider(*args, **kwargs):
            raise AssertionError("Anthropic client must not be created for an accepted summary")

        monkeypatch.setattr("app.rag._load_context", fail_context)
        monkeypatch.setattr("app.rag.anthropic_client", fail_provider)

        result = await generate_summary(pool, process_id, version_id)

        assert result == {
            "validation": {"passed": True, "errors": []},
            "model": "claude-sonnet-5",
            "generation_ms": 123,
            "usage": {},
            "cache_hit": None,
            "cost_usd": None,
            "persisted": False,
            "reused": True,
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_existing_invalid_summary_still_regenerates(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, code = await _fixture(conn)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="invalid first attempt",
                validation={"passed": False, "errors": ["bad"]},
                generation_ms=321,
            )

        context_calls = 0
        generation_calls = 0
        valid_summary = """# Resumo do processo

Resumo válido sem dados sensíveis.

## Pontos de atenção
Nenhuma divergência objetiva identificada."""

        async def fake_context(*args, **kwargs):
            nonlocal context_calls
            context_calls += 1
            return {
                "code": code,
                "court": None,
                "class_name": None,
                "subjects": [],
                "parties": [],
                "secrecy_level": 0,
                "header": {},
                "step_count": 0,
                "steps": [],
                "_process_evidence_ref": process_evidence_ref(version_id),
                "_selected_sources": [],
                "_attachment_sources": [],
            }

        async def fake_generate(client, context, validation_errors=None):
            nonlocal generation_calls
            generation_calls += 1
            _, payload, _ = _claim_material(code, version_id)
            context["_parsed_summary"] = payload
            context["_structured_summary"] = structured_summary_document(
                payload, context
            )
            return valid_summary

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.rag._load_context", fake_context)
        monkeypatch.setattr("app.rag.anthropic_client", lambda api_key: object())
        monkeypatch.setattr("app.rag._generate", fake_generate)

        result = await generate_summary(pool, process_id, version_id)

        async with pool.acquire() as conn:
            stored = await conn.fetchrow(
                """
                SELECT markdown, validation
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process_id,
                version_id,
            )

        assert context_calls == 1
        assert generation_calls == 1
        assert result["validation"] == {"passed": True, "errors": []}
        assert result["persisted"] is True
        assert result["reused"] is False
        assert stored is not None
        assert stored["markdown"] == valid_summary
        assert stored["validation"]["passed"] is True
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_existing_incomplete_provenance_is_repaired_with_same_model(
    monkeypatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, code = await _fixture(conn)
            claim_context, payload, claims = _claim_material(code, version_id)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="summary with provenance that will be damaged",
                validation={"passed": True, "errors": []},
                generation_ms=111,
                structured_output=structured_summary_document(
                    payload, claim_context
                ),
                claims=claims,
                evidence_sources=evidence_catalog(claim_context),
            )
            summary_id = await conn.fetchval(
                """
                SELECT id FROM process_summaries
                WHERE process_id=$1 AND version_id=$2
                """,
                process_id,
                version_id,
            )
            await conn.execute(
                """
                DELETE FROM process_summary_claims
                WHERE summary_id=$1 AND claim_id='current_status'
                """,
                summary_id,
            )

        context_calls = 0
        generation_calls = 0
        repaired_summary = """# Resumo do processo

Resumo válido sem dados sensíveis.

## Pontos de atenção
Nenhuma divergência objetiva identificada."""

        async def fake_context(*args, **kwargs):
            nonlocal context_calls
            context_calls += 1
            return {
                "code": code,
                "court": None,
                "class_name": None,
                "subjects": [],
                "parties": [],
                "secrecy_level": 0,
                "header": {},
                "step_count": 0,
                "steps": [],
                "_process_evidence_ref": process_evidence_ref(version_id),
                "_selected_sources": [],
                "_attachment_sources": [],
            }

        async def fake_generate(client, context, validation_errors=None):
            nonlocal generation_calls
            generation_calls += 1
            _, claim_payload, _ = _claim_material(code, version_id)
            context["_parsed_summary"] = claim_payload
            context["_structured_summary"] = structured_summary_document(
                claim_payload, context
            )
            return repaired_summary

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.rag._load_context", fake_context)
        monkeypatch.setattr("app.rag.anthropic_client", lambda api_key: object())
        monkeypatch.setattr("app.rag._generate", fake_generate)

        result = await generate_summary(pool, process_id, version_id)

        async with pool.acquire() as conn:
            stored = await conn.fetchrow(
                """
                SELECT id, markdown, model, prompt_version
                FROM process_summaries
                WHERE process_id=$1 AND version_id=$2
                """,
                process_id,
                version_id,
            )
            claim_count = await conn.fetchval(
                "SELECT count(*) FROM process_summary_claims WHERE summary_id=$1",
                stored["id"],
            )

        assert context_calls == 1
        assert generation_calls == 1
        assert result["persisted"] is True
        assert result["reused"] is False
        assert stored["markdown"] == repaired_summary
        assert stored["model"] == "claude-sonnet-5"
        assert stored["prompt_version"] == "process-summary-v6"  # nosec B101 - pytest assertion
        assert claim_count == 3
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_approved_summary_replacement_requires_publishable_restricted_document() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, code = await _fixture(conn)
            claim_context, payload, claims = _claim_material(code, version_id)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="approved public summary",
                validation={"passed": True, "errors": []},
                generation_ms=123,
                structured_output=structured_summary_document(
                    payload, claim_context
                ),
                claims=claims,
                evidence_sources=evidence_catalog(claim_context),
            )
            await conn.execute(
                "UPDATE processes SET secrecy_level = 1 WHERE id = $1",
                process_id,
            )

            restricted_payload = {
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
            }
            restricted_context = {
                "code": code,
                "class_name": None,
                "court": None,
                "header": {},
                "parties": [],
            }
            canonical = structured_summary_document(
                restricted_payload,
                restricted_context,
            )
            malformed = {**canonical, "debug": "must not replace approved summary"}

            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="malformed restricted replacement",
                validation={"passed": True, "errors": []},
                generation_ms=1,
                model=RESTRICTED_MODEL,
                prompt_version=RESTRICTED_PROMPT_VERSION,
                structured_output=malformed,
            ) is False

            unchanged = await conn.fetchrow(
                """
                SELECT markdown, model, prompt_version
                FROM process_summaries
                WHERE process_id=$1 AND version_id=$2
                """,
                process_id,
                version_id,
            )
            assert unchanged["markdown"] == "approved public summary"
            assert unchanged["model"] == "claude-sonnet-5"
            assert unchanged["prompt_version"] == "process-summary-v6"  # nosec B101 - pytest assertion

            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="canonical restricted replacement",
                validation={"passed": True, "errors": []},
                generation_ms=1,
                model=RESTRICTED_MODEL,
                prompt_version=RESTRICTED_PROMPT_VERSION,
                structured_output=canonical,
            ) is True

            replaced = await conn.fetchrow(
                """
                SELECT markdown, model, prompt_version
                FROM process_summaries
                WHERE process_id=$1 AND version_id=$2
                """,
                process_id,
                version_id,
            )
            assert replaced["markdown"] == "canonical restricted replacement"
            assert replaced["model"] == RESTRICTED_MODEL
            assert replaced["prompt_version"] == RESTRICTED_PROMPT_VERSION
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_public_summary_is_not_reused_after_process_becomes_secret(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, code = await _fixture(conn)
            claim_context, payload, claims = _claim_material(code, version_id)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="public summary that must not survive secrecy",
                validation={"passed": True, "errors": []},
                generation_ms=123,
                structured_output=structured_summary_document(
                    payload, claim_context
                ),
                claims=claims,
                evidence_sources=evidence_catalog(claim_context),
            )
            await conn.execute(
                "UPDATE processes SET secrecy_level = 1 WHERE id = $1",
                process_id,
            )

        context_calls = 0

        async def secret_context(*args, **kwargs):
            nonlocal context_calls
            context_calls += 1
            return {
                "code": code,
                "court": None,
                "class_name": None,
                "subjects": [],
                "parties": [],
                "validation_parties": [],
                "secrecy_level": 1,
                "header": {},
                "step_count": 0,
                "steps": [],
                "_selected_sources": [],
                "_attachment_sources": [],
                "_glossary_sources": [],
            }

        def fail_provider(*args, **kwargs):
            raise AssertionError("secret regeneration must not create an Anthropic client")

        monkeypatch.setattr("app.rag._load_context", secret_context)
        monkeypatch.setattr("app.rag.anthropic_client", fail_provider)

        result = await generate_summary(pool, process_id, version_id)

        async with pool.acquire() as conn:
            stored = await conn.fetchrow(
                """
                SELECT markdown, model, prompt_version
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process_id,
                version_id,
            )
            claim_count = await conn.fetchval(
                "SELECT count(*) FROM process_summary_claims WHERE summary_id = ("
                "SELECT id FROM process_summaries WHERE process_id = $1 AND version_id = $2"
                ")",
                process_id,
                version_id,
            )

        assert context_calls == 1
        assert result["model"] == "local-deterministic"
        assert result["persisted"] is True
        assert result["reused"] is False
        assert stored is not None
        assert stored["model"] == "local-deterministic"
        assert stored["prompt_version"] == RESTRICTED_PROMPT_VERSION
        assert "detalhes processuais foram restringidos por sigilo" in stored["markdown"]
        assert claim_count == 0
    finally:
        await pool.close()
