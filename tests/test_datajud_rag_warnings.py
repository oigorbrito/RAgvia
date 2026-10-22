from uuid import uuid4

import pytest

import app.rag as rag
from app.datajud_provenance import datajud_conflict_warning


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


@pytest.mark.asyncio
async def test_datajud_conflicts_become_required_source_warnings(monkeypatch) -> None:
    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.2026.8.21.0146",
            "court": "TJRS",
            "class_name": "Classe Oficial",
            "subjects": [],
            "parties": [],
            "secrecy_level": 0,
            "header": {},
            "_datajud_conflict_fields": ["class_name", "county"],
        }

    async def fake_load_steps(conn, *, version_id):
        return []

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert datajud_conflict_warning("class_name") in context["source_warnings"]
    assert datajud_conflict_warning("county") in context["source_warnings"]
    assert rag.EMPTY_STEPS_WARNING in context["source_warnings"]


@pytest.mark.asyncio
async def test_secret_context_does_not_surface_datajud_conflict_details(monkeypatch) -> None:
    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.2026.8.21.0146",
            "court": "TJRS",
            "class_name": "Classe permitida",
            "subjects": [],
            "parties": [],
            "secrecy_level": 1,
            "header": {"instance": "1"},
            "_datajud_conflict_fields": ["class_name"],
        }

    monkeypatch.setattr(rag, "_load_process", fake_load_process)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert "source_warnings" not in context
    assert "_datajud_conflict_fields" not in context
