from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

import app.rag as rag
from app.db import create_pool
from app.judit import extract_promotable_fields
from app.migrations import migrate
from app.processes import finalize_version, log_access, stage_version
from app.retrieval import lexical_search, vector_search
from app.scheduler import expurgar

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_secret_ingest_retains_raw_source_but_blocks_normalized_retrieval_and_expunge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    code = "0000000-00.2026.8.21.0150"
    request_id = f"secret-ingest-{uuid4()}"
    restricted_party = "SENTINELA-PARTE-SIGILOSA"
    restricted_subject = "SENTINELA-ASSUNTO-SIGILOSO"
    restricted_step = "SENTINELA-MOVIMENTO-SIGILOSO"
    restricted_name = "SENTINELA-NOME-HEADER"
    raw_payload = {
        "event_type": "response_created",
        "payload": {
            "request_id": request_id,
            "response_id": f"response-{uuid4()}",
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "secrecy_level": 1,
                "name": restricted_name,
                "amount": 999999,
                "instance": 1,
                "area": "Cível",
                "justice_description": "Justiça Estadual",
                "county": "Porto Alegre",
                "state": "RS",
                "city": "Porto Alegre",
                "classifications": [{"name": "Procedimento sob sigilo"}],
                "courts": [{"name": "TJRS"}],
                "parties": [{"name": restricted_party}],
                "subjects": [{"code": "secret", "name": restricted_subject}],
                "steps": [
                    {
                        "step_id": "secret-step-1",
                        "step_date": "2026-09-16T12:00:00Z",
                        "step_type": "SEGREDO",
                        "content": restricted_step,
                    }
                ],
            },
        },
    }
    response_data = raw_payload["payload"]["response_data"]
    fields = extract_promotable_fields(response_data)

    assert fields["secrecy_level"] == 1
    assert fields["parties"] == []
    assert fields["subjects"] == []
    assert fields["steps"] == []
    assert fields["header"] == {
        "instance": 1,
        "area": "Cível",
        "justice_description": "Justiça Estadual",
        "county": "Porto Alegre",
        "state": "RS",
        "city": "Porto Alegre",
    }

    async def provider_must_not_be_called(*args, **kwargs):
        raise AssertionError("secret summary must remain local and provider-free")

    monkeypatch.setattr(rag, "anthropic_client", provider_must_not_be_called)
    monkeypatch.setattr(rag, "embed_query", provider_must_not_be_called)
    monkeypatch.setattr(rag, "ensure_step_embeddings", provider_must_not_be_called)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                         tenant_processes, access_log, process_versions, processes,
                         tenants
                RESTART IDENTITY CASCADE
                """
            )
            process_id, version_id = await stage_version(
                conn,
                code=code,
                source_request_id=request_id,
                cached_response=False,
                payload=raw_payload,
                judit_request_id=request_id,
                judit_response_id=str(raw_payload["payload"]["response_id"]),
            )
            promoted = await finalize_version(
                conn,
                process_id=process_id,
                version_id=version_id,
                **fields,
            )
            assert promoted is True

            # The raw Judit source is retained by policy, but this is the only
            # persisted place where the restricted sentinels may remain.
            source_payload = await conn.fetchval(
                "SELECT source_payload FROM process_versions WHERE id = $1",
                version_id,
            )
            raw_serialized = json.dumps(source_payload, ensure_ascii=False, default=str)
            assert restricted_party in raw_serialized
            assert restricted_subject in raw_serialized
            assert restricted_step in raw_serialized
            assert restricted_name in raw_serialized

            process = await conn.fetchrow(
                """
                SELECT parties, subjects, header, secrecy_level
                FROM processes
                WHERE id = $1
                """,
                process_id,
            )
            assert process is not None
            normalized_serialized = json.dumps(dict(process), ensure_ascii=False, default=str)
            for sentinel in (
                restricted_party,
                restricted_subject,
                restricted_step,
                restricted_name,
            ):
                assert sentinel not in normalized_serialized

            step_count = await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE process_id = $1",
                process_id,
            )
            embedded_count = await conn.fetchval(
                """
                SELECT count(*)
                FROM process_steps
                WHERE process_id = $1 AND embedding IS NOT NULL
                """,
                process_id,
            )
            assert step_count == 0
            assert embedded_count == 0

            lexical = await lexical_search(
                conn,
                version_id=version_id,
                query=restricted_step,
                limit=10,
            )
            vector = await vector_search(
                conn,
                version_id=version_id,
                embedding=[0.0] * 1536,
                limit=10,
            )
            assert lexical == {}
            assert vector == {}

        context = await rag._load_context(pool, process_id, version_id)
        assert context["secrecy_level"] == 1
        assert context["parties"] == []
        assert context["subjects"] == []
        assert context["steps"] == []
        assert "validation_parties" in context
        assert context["validation_parties"] == []

        result = await rag.generate_summary(pool, process_id, version_id)
        assert result["validation"]["passed"] is True

        async with pool.acquire() as conn:
            summary = await conn.fetchval(
                "SELECT markdown FROM process_summaries WHERE process_id = $1 AND version_id = $2",
                process_id,
                version_id,
            )
            assert summary is not None
            for sentinel in (
                restricted_party,
                restricted_subject,
                restricted_step,
                restricted_name,
            ):
                assert sentinel not in summary

            tenant_id = await conn.fetchval(
                "INSERT INTO tenants(name) VALUES('Secret audit tenant') RETURNING id"
            )
            await log_access(
                conn,
                tenant_id=tenant_id,
                process_id=process_id,
                process_code=code,
                action="secret_summary_read",
                metadata={"secrecy_level": 1, "content_exposed": False},
            )
            audit_text = await conn.fetchval(
                "SELECT string_agg(metadata::text, ' ') FROM access_log WHERE process_id = $1",
                process_id,
            )
            for sentinel in (
                restricted_party,
                restricted_subject,
                restricted_step,
                restricted_name,
            ):
                assert sentinel not in (audit_text or "")

            await conn.execute(
                "UPDATE processes SET updated_at = NOW() - INTERVAL '10 days' WHERE id = $1",
                process_id,
            )
            assert await expurgar(conn, retention_days=1) == 1
            assert await conn.fetchval("SELECT count(*) FROM processes WHERE id = $1", process_id) == 0
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM process_versions WHERE process_id = $1",
                    process_id,
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM process_steps WHERE process_id = $1",
                    process_id,
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM process_summaries WHERE process_id = $1",
                    process_id,
                )
                == 0
            )
            # Audit survives expunge by design, but contains only non-content metadata.
            assert await conn.fetchval(
                "SELECT count(*) FROM access_log WHERE process_id = $1",
                process_id,
            ) == 1
    finally:
        await pool.close()
