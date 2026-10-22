from __future__ import annotations

import os
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.claim_evidence import (
    EvidenceSource,
    MaterialClaim,
    claim_evidence_is_complete,
    load_summary_claim_evidence,
    replace_summary_claim_evidence,
)
from app.claim_verification import ClaimRelationVerification, ClaimVerification
from app.migrations import migrate


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _ref(prefix: str, value: UUID) -> str:
    return f"{prefix}-{value.hex}"


async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        TRUNCATE process_summary_claim_sources, process_summary_claims,
                 process_summary_attachment_sources, process_summary_sources,
                 process_summaries, process_steps, tenant_processes,
                 process_versions, processes, tenants
        RESTART IDENTITY CASCADE
        """
    )


async def _process_version(conn: asyncpg.Connection, *, code: str):
    process_id = uuid4()
    version_id = uuid4()
    await conn.execute(
        """
        INSERT INTO processes (id, code, class_name, subjects, secrecy_level)
        VALUES ($1, $2, 'Procedimento Comum', '[]'::jsonb, 0)
        """,
        process_id,
        code,
    )
    await conn.execute(
        """
        INSERT INTO process_versions (id, process_id, source_request_id, finalized)
        VALUES ($1, $2, $3, TRUE)
        """,
        version_id,
        process_id,
        f"req-{version_id}",
    )
    await conn.execute(
        "UPDATE processes SET current_version_id=$2 WHERE id=$1",
        process_id,
        version_id,
    )
    return process_id, version_id


@pytest.mark.asyncio
async def test_claim_verification_round_trips_with_persisted_evidence() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2499"
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        process_ref = _ref("p", version_id)
        claim = MaterialClaim(
            claim_id="current_status",
            claim_class="current_status",
            text="Situação atual registrada.",
            evidence_refs=(process_ref,),
        )
        relation = ClaimRelationVerification(
            evidence_ref=process_ref,
            status="supported",
            reason="exact_text_present",
            evidence_excerpt='{"status":"Situação atual registrada."}',
            evidence_excerpt_sha256=(
                "7f8f7656b893152e240a6c29197486733570225eb0304866052553fb2588d77c"
            ),
        )
        verification = ClaimVerification(
            claim_id="current_status",
            claim_class="current_status",
            status="supported",
            reason="at_least_one_cited_source_supports_deterministic_fact",
            deterministic_fact_count=1,
            relations=(relation,),
        )

        await replace_summary_claim_evidence(
            conn,
            summary_id=summary_id,
            process_id=process_id,
            version_id=version_id,
            claims=[claim],
            catalog={
                process_ref: EvidenceSource(
                    evidence_ref=process_ref,
                    kind="process",
                )
            },
            verification={"current_status": verification},
        )

        loaded = await load_summary_claim_evidence(conn, summary_id=summary_id)
        stored_excerpt = await conn.fetchval(
            """
            SELECT evidence_excerpt
            FROM process_summary_claim_evidence_excerpts
            WHERE claim_row_id = (
                SELECT id
                FROM process_summary_claims
                WHERE summary_id=$1 AND claim_id='current_status'
            )
              AND evidence_ref=$2
            """,
            summary_id,
            process_ref,
        )

        assert stored_excerpt == '{"status":"Situação atual registrada."}'
        assert all(
            "evidence_excerpt" not in source
            for item in loaded
            for source in item["sources"]
        )
        assert loaded == [
            {
                "claim_id": "current_status",
                "claim_class": "current_status",
                "text": "Situação atual registrada.",
                "evidence_refs": [process_ref],
                "verification_status": "supported",
                "verification_reason": (
                    "at_least_one_cited_source_supports_deterministic_fact"
                ),
                "sources": [
                    {
                        "evidence_ref": process_ref,
                        "source_kind": "process",
                        "source_order": 0,
                        "verification_status": "supported",
                        "verification_reason": "exact_text_present",
                        "evidence_excerpt_sha256": (
                            "7f8f7656b893152e240a6c29197486733570225eb0304866052553fb2588d77c"
                        ),
                        "page_start": None,
                        "page_end": None,
                        "char_start": None,
                        "char_end": None,
                    }
                ],
            }
        ]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_partial_used_source_loss_breaks_declared_claim_completeness() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2500"
        )
        step_id = uuid4()
        await conn.execute(
            """
            INSERT INTO process_steps (
                id, version_id, process_id, step_number, title, text
            ) VALUES ($1, $2, $3, 1, 'Movimento', 'Movimento relevante.')
            """,
            step_id,
            version_id,
            process_id,
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summary_sources (
                summary_id, process_id, version_id, chunk_type,
                step_id, step_number, source_order
            ) VALUES ($1, $2, $3, 'movement', $4, 1, 0)
            """,
            summary_id,
            process_id,
            version_id,
            step_id,
        )

        synthesis_claim_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id,
                claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'synthesis', 'synthesis', 'Síntese.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        status_claim_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id,
                claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'current_status', 'current_status', 'Situação.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        attention_claim_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id,
                claim_id, claim_class, claim_text
            ) VALUES (
                $1, $2, $3, 'attention:0', 'attention', 'Sem divergência material.'
            )
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        process_ref = _ref("p", version_id)
        movement_ref = _ref("m", step_id)
        await conn.executemany(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, step_id, source_order
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            [
                (
                    synthesis_claim_id,
                    summary_id,
                    process_id,
                    version_id,
                    process_ref,
                    "process",
                    None,
                    0,
                ),
                (
                    synthesis_claim_id,
                    summary_id,
                    process_id,
                    version_id,
                    movement_ref,
                    "movement",
                    step_id,
                    1,
                ),
                (
                    status_claim_id,
                    summary_id,
                    process_id,
                    version_id,
                    process_ref,
                    "process",
                    None,
                    0,
                ),
                (
                    attention_claim_id,
                    summary_id,
                    process_id,
                    version_id,
                    process_ref,
                    "process",
                    None,
                    0,
                ),
            ],
        )
        structured_output = {
            "schema_version": 2,
            "process": {
                "cnj": "0000000-00.2026.8.21.2500",
                "class_name": None,
                "court": None,
                "header": {},
                "parties": [],
            },
            "summary": {
                "synthesis": "Síntese.",
                "timeline": [],
                "current_status": "Situação.",
                "attention": ["Sem divergência material."],
                "decisions": [],
                "deadlines": [],
                "related_processes": [],
                "attachments": [],
                "claims": [
                    {
                        "claim_id": "synthesis",
                        "text": "Síntese.",
                        "evidence_refs": [process_ref, movement_ref],
                    },
                    {
                        "claim_id": "current_status",
                        "text": "Situação.",
                        "evidence_refs": [process_ref],
                    },
                    {
                        "claim_id": "attention:0",
                        "text": "Sem divergência material.",
                        "evidence_refs": [process_ref],
                    },
                ],
            },
        }

        loaded = await load_summary_claim_evidence(conn, summary_id=summary_id)
        assert claim_evidence_is_complete(structured_output, loaded) is True

        await conn.execute(
            """
            UPDATE process_summary_claims
            SET claim_class='decision'
            WHERE id=$1
            """,
            synthesis_claim_id,
        )
        wrong_class = await load_summary_claim_evidence(
            conn, summary_id=summary_id
        )
        assert claim_evidence_is_complete(structured_output, wrong_class) is False
        await conn.execute(
            """
            UPDATE process_summary_claims
            SET claim_class='synthesis'
            WHERE id=$1
            """,
            synthesis_claim_id,
        )

        await conn.execute(
            "DELETE FROM process_summary_sources WHERE summary_id=$1 AND step_id=$2",
            summary_id,
            step_id,
        )
        partially_tampered = await load_summary_claim_evidence(
            conn, summary_id=summary_id
        )
        synthesis = next(
            item
            for item in partially_tampered
            if item["claim_id"] == "synthesis"
        )
        assert synthesis["evidence_refs"] == [process_ref]
        assert claim_evidence_is_complete(
            structured_output,
            partially_tampered,
        ) is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_claim_source_trigger_binds_ref_and_underlying_movement_scope() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2501"
        )
        other_process_id, other_version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2502"
        )

        step_id = uuid4()
        other_step_id = uuid4()
        await conn.executemany(
            """
            INSERT INTO process_steps (
                id, version_id, process_id, step_number, title, text
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [
                (step_id, version_id, process_id, 1, "Distribuição", "Distribuído."),
                (
                    other_step_id,
                    other_version_id,
                    other_process_id,
                    1,
                    "Outro movimento",
                    "Movimento de outro processo.",
                ),
            ],
        )

        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summary_sources (
                summary_id, process_id, version_id, chunk_type,
                step_id, step_number, source_order
            ) VALUES ($1, $2, $3, 'movement', $4, 1, 0)
            """,
            summary_id,
            process_id,
            version_id,
            step_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'timeline:0', 'procedural_event', 'Distribuído.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )

        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, step_id, source_order
            ) VALUES ($1, $2, $3, $4, $5, 'movement', $6, 0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("m", step_id),
            step_id,
        )

        loaded = await load_summary_claim_evidence(conn, summary_id=summary_id)
        assert loaded[0]["evidence_refs"] == [_ref("m", step_id)]

        await conn.execute(
            "DELETE FROM process_summary_sources WHERE summary_id=$1 AND step_id=$2",
            summary_id,
            step_id,
        )
        tampered = await load_summary_claim_evidence(conn, summary_id=summary_id)
        assert tampered[0]["evidence_refs"] == []
        await conn.execute(
            """
            INSERT INTO process_summary_sources (
                summary_id, process_id, version_id, chunk_type,
                step_id, step_number, source_order
            ) VALUES ($1, $2, $3, 'movement', $4, 1, 0)
            """,
            summary_id,
            process_id,
            version_id,
            step_id,
        )

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, step_id, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'movement', $6, 1)
                """,
                claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("m", uuid4()),
                step_id,
            )

        # The legacy summary-source table can represent an inconsistent row.
        # Claim provenance must still reject it by checking process_steps directly.
        await conn.execute(
            """
            INSERT INTO process_summary_sources (
                summary_id, process_id, version_id, chunk_type,
                step_id, step_number, source_order
            ) VALUES ($1, $2, $3, 'movement', $4, 1, 1)
            """,
            summary_id,
            process_id,
            version_id,
            other_step_id,
        )
        other_claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'current_status', 'current_status', 'Outro movimento.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, step_id, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'movement', $6, 0)
                """,
                other_claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("m", other_step_id),
                other_step_id,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_claim_ref_must_match_summary_version() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2503"
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'synthesis', 'synthesis', 'Síntese.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )

        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, source_order
            ) VALUES ($1, $2, $3, $4, $5, 'process', 0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("p", version_id),
        )

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'process', 1)
                """,
                claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("p", uuid4()),
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_attachment_claim_ref_must_match_authorized_chunk_scope() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2504"
        )
        other_process_id, other_version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2505"
        )

        attachment_id = await conn.fetchval(
            """
            INSERT INTO process_attachments (
                process_id, version_id, source_attachment_id, status, content_type
            ) VALUES ($1,$2,'doc-main','ready','text/plain')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        chunk_id = await conn.fetchval(
            """
            INSERT INTO attachment_chunks (
                attachment_id, process_id, version_id, chunk_index,
                text, content_sha256
            ) VALUES ($1,$2,$3,0,'Documento principal.',
                      repeat('a', 64))
            RETURNING id
            """,
            attachment_id,
            process_id,
            version_id,
        )

        other_attachment_id = await conn.fetchval(
            """
            INSERT INTO process_attachments (
                process_id, version_id, source_attachment_id, status, content_type
            ) VALUES ($1,$2,'doc-other','ready','text/plain')
            RETURNING id
            """,
            other_process_id,
            other_version_id,
        )
        other_chunk_id = await conn.fetchval(
            """
            INSERT INTO attachment_chunks (
                attachment_id, process_id, version_id, chunk_index,
                text, content_sha256
            ) VALUES ($1,$2,$3,0,'Documento de outro processo.',
                      repeat('b', 64))
            RETURNING id
            """,
            other_attachment_id,
            other_process_id,
            other_version_id,
        )

        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1,$2,'# resumo','{"passed":true}'::jsonb,'fake','test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summary_attachment_sources (
                summary_id, process_id, version_id, attachment_id,
                attachment_chunk_id, source_attachment_id, content_sha256, source_order
            ) VALUES ($1,$2,$3,$4,$5,'doc-main',repeat('a',64),0)
            """,
            summary_id,
            process_id,
            version_id,
            attachment_id,
            chunk_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1,$2,$3,'attachments:0','attachment','Documento principal.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )

        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, attachment_chunk_id, source_order
            ) VALUES ($1,$2,$3,$4,$5,'attachment',$6,0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("a", chunk_id),
            chunk_id,
        )

        loaded = await load_summary_claim_evidence(conn, summary_id=summary_id)
        assert loaded[0]["evidence_refs"] == [_ref("a", chunk_id)]

        await conn.execute(
            """
            DELETE FROM process_summary_attachment_sources
            WHERE summary_id=$1 AND attachment_chunk_id=$2
            """,
            summary_id,
            chunk_id,
        )
        tampered = await load_summary_claim_evidence(conn, summary_id=summary_id)
        assert tampered[0]["evidence_refs"] == []
        await conn.execute(
            """
            INSERT INTO process_summary_attachment_sources (
                summary_id, process_id, version_id, attachment_id,
                attachment_chunk_id, source_attachment_id, content_sha256, source_order
            ) VALUES ($1,$2,$3,$4,$5,'doc-main',repeat('a',64),0)
            """,
            summary_id,
            process_id,
            version_id,
            attachment_id,
            chunk_id,
        )

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                UPDATE process_summary_claim_sources
                SET evidence_ref=$2
                WHERE claim_row_id=$1
                """,
                claim_row_id,
                _ref("a", uuid4()),
            )

        # Even if a legacy provenance row is inconsistent, claim provenance
        # must verify the underlying attachment chunk scope directly.
        await conn.execute("ALTER TABLE process_summary_attachment_sources DISABLE TRIGGER process_summary_attachment_sources_scope_check")
        try:
            await conn.execute(
                """
                INSERT INTO process_summary_attachment_sources (
                    summary_id, process_id, version_id, attachment_id,
                    attachment_chunk_id, source_attachment_id, content_sha256, source_order
                ) VALUES ($1,$2,$3,$4,$5,'doc-other',repeat('b',64),1)
                """,
                summary_id,
                process_id,
                version_id,
                other_attachment_id,
                other_chunk_id,
            )
        finally:
            await conn.execute("ALTER TABLE process_summary_attachment_sources ENABLE TRIGGER process_summary_attachment_sources_scope_check")

        other_claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1,$2,$3,'attachments:1','attachment','Documento de outro processo.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, attachment_chunk_id, source_order
                ) VALUES ($1,$2,$3,$4,$5,'attachment',$6,0)
                """,
                other_claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("a", other_chunk_id),
                other_chunk_id,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_claim_provenance_cascades_when_summary_is_deleted() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2506"
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1,$2,'# resumo','{"passed":true}'::jsonb,'fake','test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1,$2,$3,'synthesis','synthesis','Síntese.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
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
            _ref("p", version_id),
        )

        await conn.execute("DELETE FROM process_summaries WHERE id=$1", summary_id)

        assert await conn.fetchval(
            "SELECT count(*) FROM process_summary_claims WHERE summary_id=$1",
            summary_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM process_summary_claim_sources WHERE summary_id=$1",
            summary_id,
        ) == 0
    finally:
        await conn.close()
