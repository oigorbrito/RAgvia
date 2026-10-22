from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from app.datajud_enrichment import DataJudMetadata, merge_datajud_metadata
from app.datajud_provenance import (
    load_datajud_conflict_warnings,
    load_datajud_field_provenance,
    replace_datajud_field_provenance,
)
from app.migrations import migrate
from app.rag import _load_process

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_datajud_field_provenance_is_version_scoped_and_replaceable() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process_id = await conn.fetchval(
            "INSERT INTO processes (code) VALUES ($1) RETURNING id",
            "0000000-00.2026.8.21.0146",
        )
        version_id = await conn.fetchval(
            """
            INSERT INTO process_versions (process_id, source_request_id)
            VALUES ($1, $2)
            RETURNING id
            """,
            process_id,
            f"datajud-prov-{uuid4()}",
        )

        judit = {
            "header": {"class_code": "OLD", "county": "Porto Alegre"},
            "parties": [{"name": "sentinel-private-party"}],
            "subjects": [{"code": "1", "name": "Assunto Judit"}],
            "steps": [{"step_number": 1, "text": "sentinel-private-step"}],
            "class_name": "Classe Judit",
            "court": "TJRS",
            "secrecy_level": 0,
        }
        result = merge_datajud_metadata(
            judit,
            DataJudMetadata(
                class_name="Classe Oficial",
                class_code="7",
                subjects=({"code": "5804", "name": "Assunto Oficial"},),
                adjudicating_body="1ª Vara Cível",
                county="Canoas",
                source_ref="DataJud fixture 146",
            ),
        )

        await replace_datajud_field_provenance(
            conn,
            process_id=process_id,
            version_id=version_id,
            result=result,
        )
        rows = await load_datajud_field_provenance(
            conn,
            process_id=process_id,
            version_id=version_id,
        )

        assert {row["field"] for row in rows} == {
            "class_name",
            "class_code",
            "subjects",
            "adjudicating_body",
            "county",
        }
        assert all(row["selected_source"] == "datajud" for row in rows)
        assert {row["field"] for row in rows if row["conflict"]} == {
            "class_name",
            "class_code",
            "subjects",
            "county",
        }
        rendered = repr(rows)
        assert "sentinel-private-party" not in rendered
        assert "sentinel-private-step" not in rendered

        warnings = await load_datajud_conflict_warnings(
            conn,
            process_id=process_id,
            version_id=version_id,
        )
        assert len(warnings) == 4
        assert all("Judit e DataJud" in warning for warning in warnings)

        fallback = merge_datajud_metadata(judit, DataJudMetadata())
        await replace_datajud_field_provenance(
            conn,
            process_id=process_id,
            version_id=version_id,
            result=fallback,
        )
        replaced = await load_datajud_field_provenance(
            conn,
            process_id=process_id,
            version_id=version_id,
        )
        assert len(replaced) == 5
        assert all(row["selected_source"] == "judit" for row in replaced)
        assert await load_datajud_conflict_warnings(
            conn,
            process_id=process_id,
            version_id=version_id,
        ) == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_datajud_provenance_rejects_cross_process_version_scope() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process_a = await conn.fetchval(
            "INSERT INTO processes (code) VALUES ($1) RETURNING id",
            "0000000-00.2026.8.21.0147",
        )
        process_b = await conn.fetchval(
            "INSERT INTO processes (code) VALUES ($1) RETURNING id",
            "0000000-00.2026.8.21.0148",
        )
        version_a = await conn.fetchval(
            """
            INSERT INTO process_versions (process_id, source_request_id)
            VALUES ($1, $2)
            RETURNING id
            """,
            process_a,
            f"datajud-scope-{uuid4()}",
        )

        with pytest.raises(asyncpg.RaiseError, match="scope does not match"):
            await conn.execute(
                """
                INSERT INTO process_datajud_field_provenance (
                    process_id, version_id, field_name, selected_source,
                    selected_value, conflict, source_ref
                )
                VALUES ($1, $2, 'class_name', 'datajud', '"Classe"'::jsonb, FALSE, 'fixture')
                """,
                process_b,
                version_a,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rag_load_process_reads_only_persisted_datajud_conflict_fields() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id = await conn.fetchval(
                """
                INSERT INTO processes (code, class_name, header)
                VALUES ($1, 'Classe Oficial', '{"county":"Canoas"}'::jsonb)
                RETURNING id
                """,
                "0000000-00.2026.8.21.0149",
            )
            version_id = await conn.fetchval(
                """
                INSERT INTO process_versions (process_id, source_request_id, finalized)
                VALUES ($1, $2, TRUE)
                RETURNING id
                """,
                process_id,
                f"datajud-rag-{uuid4()}",
            )
            await conn.execute(
                "UPDATE processes SET current_version_id=$2 WHERE id=$1",
                process_id,
                version_id,
            )
            await conn.executemany(
                """
                INSERT INTO process_datajud_field_provenance (
                    process_id, version_id, field_name, selected_source,
                    selected_value, conflict, source_ref
                )
                VALUES ($1, $2, $3, 'datajud', $4::jsonb, $5, 'DataJud fixture 146')
                """,
                [
                    (process_id, version_id, "class_name", '"Classe Oficial"', True),
                    (process_id, version_id, "county", '"Canoas"', True),
                    (process_id, version_id, "class_code", '"7"', False),
                ],
            )

        loaded = await _load_process(pool, process_id, version_id)

        assert loaded["_datajud_conflict_fields"] == ["class_name", "county"]
        assert loaded["class_name"] == "Classe Oficial"
        assert loaded["header"]["county"] == "Canoas"
    finally:
        await pool.close()
