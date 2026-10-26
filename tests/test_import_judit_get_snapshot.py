from __future__ import annotations

import json
from uuid import uuid4

import pytest

from scripts.import_judit_get_snapshot import (
    SnapshotRejected,
    stage_snapshot,
    validate_snapshot,
)


def code() -> str:
    prefix, suffix = "0000123", "20268160021"
    check = 98 - (int(prefix + suffix + "00") % 97)
    return f"{prefix}-{check:02d}.2026.8.16.0021"


def capture(*, provider_summary: bool = True) -> bytes:
    request_id = str(uuid4())
    lawsuit = {
        "request_id": request_id,
        "response_id": str(uuid4()),
        "response_type": "lawsuit",
        "response_data": {
            "code": code(),
            "secrecy_level": 0,
            "parties": [{"name": "Parte Teste", "main_document": "12345678901"}],
            "steps": [{"step_id": "step-1", "content": "Distribuido", "private": False}],
            "attachments": [{"attachment_id": "a", "private": False}],
        },
        "cached": False,
        "tags": {"callback_url": "https://example.invalid/private-token"},
    }
    items = [lawsuit]
    if provider_summary:
        items.append({
            "request_id": request_id, "response_id": str(uuid4()),
            "response_type": "summary",
            "response_data": {"data": ["EXTERNAL_SUMMARY_SENTINEL"]},
        })
    return json.dumps({
        "request_status": "completed",
        "page": 1,
        "page_count": len(items),
        "all_count": len(items),
        "all_pages_count": 1,
        "page_data": items,
    }, ensure_ascii=False).encode()


def test_snapshot_validation_keeps_only_lawsuit_and_get_provenance() -> None:
    raw = capture()
    selected = validate_snapshot(raw, expected_cnj=code())
    serialized = json.dumps(selected.payload, ensure_ascii=False)
    assert selected.step_count == 1
    assert selected.payload["ingest_provenance"]["webhook_authenticated"] is False
    assert selected.payload["ingest_provenance"]["kind"] == "operator_supplied_judit_get_snapshot"
    assert selected.payload["response_type"] == "lawsuit"
    assert "EXTERNAL_SUMMARY_SENTINEL" not in serialized
    assert "private-token" not in serialized
    assert "callback_id" not in serialized
    assert "request_created_at" not in serialized


@pytest.mark.parametrize("mutation,reason", [
    (lambda d: d.update(request_status="processing"), "request_not_completed"),
    (lambda d: d.update(all_pages_count=2), "incomplete_or_inconsistent_pages"),
    (lambda d: d.update(page_count=1), "incomplete_or_inconsistent_pages"),
    (lambda d: d["page_data"][0]["response_data"].update(secrecy_level=1), "restricted_lawsuit"),
    (lambda d: d["page_data"][0]["response_data"].update(secrecy_level=0.1), "unknown_secrecy_level"),
    (lambda d: d["page_data"][0].update(cached="false"), "ambiguous_cached_flag"),
    (lambda d: d["page_data"][0]["response_data"]["steps"][0].update(private=True), "private_or_unknown_step"),
    (lambda d: d["page_data"][0]["response_data"]["attachments"][0].pop("private"), "private_or_unknown_attachment"),
    (lambda d: d["page_data"][1].update(request_id=str(uuid4())), "inconsistent_request_id"),
    (lambda d: d["page_data"][0].update(response_id="bad-id"), "invalid_provider_identifier"),
])
def test_invalid_or_incomplete_capture_fails_closed(mutation, reason: str) -> None:
    document = json.loads(capture())
    mutation(document)
    with pytest.raises(SnapshotRejected, match=reason):
        validate_snapshot(json.dumps(document).encode(), expected_cnj=code())


def test_wrong_cnj_or_invalid_check_digit_rejected() -> None:
    with pytest.raises(SnapshotRejected, match="cnj_mismatch_or_invalid_checksum"):
        validate_snapshot(capture(), expected_cnj="6003160-36.2026.8.16.0021")
    broken = json.loads(capture())
    broken["page_data"][0]["response_data"]["code"] = "0000123-00.2026.8.16.0021"
    with pytest.raises(SnapshotRejected, match="cnj_mismatch_or_invalid_checksum"):
        validate_snapshot(json.dumps(broken).encode(), expected_cnj=code())


@pytest.mark.asyncio
async def test_staging_rejects_response_owned_by_other_source(monkeypatch) -> None:
    selected = validate_snapshot(capture(), expected_cnj=code())
    tenant_id = uuid4()
    executed_queries = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Connection:
        def transaction(self):
            return Transaction()

        async def execute(self, sql, *args):
            executed_queries.append(sql)

        async def fetchval(self, sql, *args):
            return True

        async def fetch(self, sql, *args):
            return []

        async def fetchrow(self, sql, *args):
            return {
                "judit_request_id": selected.request_id,
                "source_payload": {"callback_id": "authentic-webhook"},
            }

        async def close(self):
            return None

    async def fake_connect(*args, **kwargs):
        return Connection()

    async def forbidden_stage(*args, **kwargs):
        raise AssertionError("collision must block before staging")

    monkeypatch.setattr("scripts.import_judit_get_snapshot.asyncpg.connect", fake_connect)
    monkeypatch.setattr("scripts.import_judit_get_snapshot.stage_version", forbidden_stage)
    with pytest.raises(SnapshotRejected, match="response_already_owned_by_other_source"):
        await stage_snapshot(selected, database_url="postgres://unused", tenant_id=tenant_id)
    assert not any("INSERT INTO" in sql for sql in executed_queries)
