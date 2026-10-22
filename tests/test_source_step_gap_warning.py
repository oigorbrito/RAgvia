from __future__ import annotations

from uuid import uuid4

import pytest

import app.rag as rag
from app.retrieval import Step


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


def _step(internal: int, source: int | None) -> Step:
    return Step(
        id=uuid4(),
        step_number=internal,
        text=f"Movimento {internal}",
        source_step_number=source,
    )


def test_source_step_gap_warning_requires_complete_explicit_numbering() -> None:
    assert rag._source_step_gap_warnings([_step(1, 7), _step(2, None), _step(3, 9)]) == []
    assert rag._source_step_gap_warnings([_step(1, 7), _step(2, 8), _step(3, 9)]) == []


def test_source_step_gap_warning_reports_each_ascending_gap() -> None:
    warnings = rag._source_step_gap_warnings(
        [_step(1, 7), _step(2, 9), _step(3, 12)]
    )

    assert warnings == [
        "Há salto na numeração de movimentos da fonte: 7→9.",
        "Há salto na numeração de movimentos da fonte: 9→12.",
    ]


@pytest.mark.asyncio
async def test_load_context_surfaces_gap_warning_to_provider_context(monkeypatch) -> None:
    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.2026.8.21.0001",
            "court": "TJRS",
            "class_name": "Procedimento Comum",
            "subjects": [],
            "parties": [],
            "secrecy_level": 0,
            "header": {},
        }

    async def fake_load_steps(conn, *, version_id):
        return [_step(1, 7), _step(2, 9)]

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    warning = "Há salto na numeração de movimentos da fonte: 7→9."
    assert context["source_warnings"] == [warning]
    provider_process, provider_steps = rag._provider_payload(context)
    assert provider_process["source_warnings"] == [warning]
    assert len(provider_steps) == 2


def test_gap_warning_must_appear_inside_attention_section() -> None:
    warning = "Há salto na numeração de movimentos da fonte: 7→9."
    context = {
        "code": "0000000-00.2026.8.21.0001",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "subjects": [],
        "parties": [],
        "secrecy_level": 0,
        "header": {},
        "step_count": 2,
        "steps": [],
        "source_warnings": [warning],
        "_process_evidence_ref": "p-00000000000000000000000000000001",
        "_selected_sources": [],
        "_attachment_sources": [],
        "_parsed_summary": {
        "synthesis": "Síntese factual.",
        "timeline": [],
        "current_status": "Situação atual registrada.",
        "attention": ["Nenhuma divergência objetiva identificada."],
        "decisions": [],
        "deadlines": [],
        "related_processes": [],
        "attachments": [],
        "claims": [
            {
                "claim_id": "synthesis",
                "text": "Síntese factual.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
            {
                "claim_id": "current_status",
                "text": "Situação atual registrada.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
            {
                "claim_id": "attention:0",
                "text": "Nenhuma divergência objetiva identificada.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
        ],
    },
    }

    invalid = rag._validate_provider_summary(
        f"# Resumo do processo\n\n## Síntese\n{warning}\n\n## Pontos de atenção\nNenhuma divergência factual identificada.",
        context,
    )
    assert invalid.passed is False
    assert f"required attention fact missing: {warning}" in invalid.errors

    valid = rag._validate_provider_summary(
        f"# Resumo do processo\n\n## Pontos de atenção\n{warning}",
        context,
    )
    assert valid.passed is True
