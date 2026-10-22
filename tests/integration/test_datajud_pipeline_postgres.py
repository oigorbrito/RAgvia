from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

import app.judit_tasks as judit_tasks
from app.datajud_client import DataJudLookupResult
from app.datajud_enrichment import DataJudMetadata
from app.json_utils import decode_json_list, decode_json_object
from app.migrations import migrate
from app.processes import stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _event(*, request_id: str, response_id: str, code: str) -> dict:
    return {
        "callback_id": f"callback-{uuid4()}",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "classifications": [{"code": "old-7", "name": "Classe Judit"}],
                "courts": [{"name": "TJRS"}],
                "county": "Porto Alegre",
                "parties": [{"name": "Parte Judit", "side": "active"}],
                "subjects": [{"code": "1", "name": "Assunto Judit"}],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_type": "MOVIMENTO",
                        "content": "movimento Judit preservado",
                    }
                ],
            },
            "tags": {"cached_response": False},
        },
    }


@pytest.fixture(autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     process_datajud_field_provenance, tenant_processes, access_log,
                     process_versions, processes
            RESTART IDENTITY CASCADE
            """
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_finalize_applies_datajud_merge_and_provenance_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    observed: dict[str, object] = {}

    async def fake_lookup(*, code: str, secrecy_level: int, config=None):
        observed["code"] = code
        observed["secrecy_level"] = secrecy_level
        return DataJudLookupResult(
            status="ok",
            metadata=DataJudMetadata(
                class_name="Procedimento Comum Cível",
                class_code="7",
                subjects=({"code": "5804", "name": "Investigação de Paternidade"},),
                adjudicating_body="1ª Vara Cível",
                county="Canoas",
                source_ref="DataJud synthetic E2E",
            ),
        )

    monkeypatch.setattr(judit_tasks, "lookup_datajud_metadata", fake_lookup)

    request_id = f"request-{uuid4()}"
    response_id = f"response-{uuid4()}"
    code = "0000000-00.2026.8.21.0001"

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=response_id,
            cached_response=False,
            payload=_event(request_id=request_id, response_id=response_id, code=code),
            judit_request_id=request_id,
            judit_response_id=response_id,
        )
    finally:
        await conn.close()

    result = await judit_tasks.finalize_judit_request_task({"request_id": request_id})

    assert result["status"] == "finalized"
    assert result["datajud_status"] == "ok"
    assert result["summary_enqueued"] is True
    assert observed == {"code": code, "secrecy_level": 0}

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process = await conn.fetchrow(
            """
            SELECT class_name, subjects, parties, header, current_version_id
            FROM processes
            WHERE id=$1
            """,
            process_id,
        )
        step_text = await conn.fetchval(
            "SELECT text FROM process_steps WHERE version_id=$1",
            version_id,
        )
        provenance = await conn.fetch(
            """
            SELECT field_name, selected_source, conflict, source_ref
            FROM process_datajud_field_provenance
            WHERE version_id=$1
            ORDER BY field_name
            """,
            version_id,
        )
    finally:
        await conn.close()

    assert process is not None
    assert process["current_version_id"] == version_id
    assert process["class_name"] == "Procedimento Comum Cível"
    assert decode_json_list(process["subjects"]) == [
        {"code": "5804", "name": "Investigação de Paternidade"}
    ]
    assert decode_json_list(process["parties"]) == [
        {"name": "Parte Judit", "side": "active", "person_type": None}
    ]
    header = decode_json_object(process["header"])
    assert header["class_code"] == "7"
    assert header["adjudicating_body"] == "1ª Vara Cível"
    assert header["county"] == "Canoas"
    assert step_text == "movimento Judit preservado"
    assert len(provenance) == 5
    assert all(row["source_ref"] == "DataJud synthetic E2E" for row in provenance if row["selected_source"] == "datajud")
    assert {row["field_name"] for row in provenance if row["conflict"]} == {
        "class_name",
        "class_code",
        "subjects",
        "county",
    }


@pytest.mark.asyncio
async def test_finalize_keeps_judit_when_datajud_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    async def fake_lookup(*, code: str, secrecy_level: int, config=None):
        return DataJudLookupResult(status="unavailable", error_code="transport_error")

    monkeypatch.setattr(judit_tasks, "lookup_datajud_metadata", fake_lookup)

    request_id = f"request-{uuid4()}"
    response_id = f"response-{uuid4()}"
    code = "0000000-00.2026.8.21.0002"

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=response_id,
            cached_response=False,
            payload=_event(request_id=request_id, response_id=response_id, code=code),
            judit_request_id=request_id,
            judit_response_id=response_id,
        )
    finally:
        await conn.close()

    result = await judit_tasks.finalize_judit_request_task({"request_id": request_id})

    assert result["status"] == "finalized"
    assert result["datajud_status"] == "unavailable"
    assert result["summary_enqueued"] is True

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        process = await conn.fetchrow(
            "SELECT class_name, subjects, header FROM processes WHERE id=$1",
            process_id,
        )
        provenance_count = await conn.fetchval(
            "SELECT count(*) FROM process_datajud_field_provenance WHERE version_id=$1",
            version_id,
        )
    finally:
        await conn.close()

    assert process is not None
    assert process["class_name"] == "Classe Judit"
    assert decode_json_list(process["subjects"]) == [
        {"code": "1", "name": "Assunto Judit"}
    ]
    assert decode_json_object(process["header"])["county"] == "Porto Alegre"
    assert provenance_count == 0
