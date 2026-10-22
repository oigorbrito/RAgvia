from uuid import uuid4

import pytest

import app.rag as rag
from app.attachment_signals import attachment_status_flags, attachment_status_warnings


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


def test_attachment_status_flags_are_deterministic_and_aggregate_only() -> None:
    flags = attachment_status_flags(
        {
            "ready": 2,
            "pending": 1,
            "unavailable": 1,
            "corrupt": 1,
            "unreadable": 1,
        }
    )

    assert flags == {
        "attachments": {
            "total": 6,
            "status_counts": {
                "pending": 1,
                "ready": 2,
                "unavailable": 1,
                "corrupt": 1,
                "unreadable": 1,
            },
            "processing_complete": False,
            "degraded": True,
        }
    }
    assert attachment_status_flags({}) == {}


def test_attachment_status_warnings_cover_non_ready_conditions() -> None:
    warnings = attachment_status_warnings(
        {
            "ready": 3,
            "pending": 2,
            "unavailable": 1,
            "corrupt": 1,
            "unreadable": 4,
        }
    )

    assert warnings == [
        "Há anexos pendentes de processamento: 2.",
        "Há anexos indisponíveis na fonte: 1.",
        "Há anexos corrompidos: 1.",
        "Há anexos sem texto legível: 4.",
    ]


@pytest.mark.asyncio
async def test_rag_context_requires_attachment_status_warnings(monkeypatch) -> None:
    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.2026.8.21.0001",
            "court": "TJRS",
            "class_name": "Execução",
            "subjects": [],
            "parties": [],
            "secrecy_level": 0,
            "header": {},
        }

    async def fake_load_steps(conn, *, version_id):
        return []

    async def fake_load_attachment_context(
        pool,
        *,
        tenant_id,
        process_id,
        version_id,
        process_context,
        base_query,
    ):
        return (
            [],
            [],
            {"ready": 1, "corrupt": 1, "unreadable": 2},
        )

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)
    monkeypatch.setattr(rag, "load_attachment_context", fake_load_attachment_context)

    context = await rag._load_context(
        _Pool(),
        uuid4(),
        uuid4(),
        tenant_id=uuid4(),
    )

    assert context["attachment_status"] == {
        "ready": 1,
        "corrupt": 1,
        "unreadable": 2,
    }
    assert rag.EMPTY_STEPS_WARNING in context["source_warnings"]
    assert "Há anexos corrompidos: 1." in context["source_warnings"]
    assert "Há anexos sem texto legível: 2." in context["source_warnings"]
