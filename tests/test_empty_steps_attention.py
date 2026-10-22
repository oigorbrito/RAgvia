from __future__ import annotations

from uuid import uuid4

import pytest

import app.rag as rag


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


@pytest.mark.asyncio
async def test_empty_steps_adds_factual_source_warning_to_provider_context(monkeypatch) -> None:
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
        return []

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert context["step_count"] == 0
    assert context["steps"] == []
    assert context["source_warnings"] == [rag.EMPTY_STEPS_WARNING]

    provider_process, provider_steps = rag._provider_payload(context)
    assert provider_process["source_warnings"] == [rag.EMPTY_STEPS_WARNING]
    assert provider_steps == []


@pytest.mark.asyncio
async def test_cached_source_adds_possible_staleness_warning(monkeypatch) -> None:
    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.2026.8.21.0001",
            "court": "TJRS",
            "class_name": "Procedimento Comum",
            "subjects": [],
            "parties": [],
            "representatives": [],
            "secrecy_level": 0,
            "header": {},
            "_source_cached_response": True,
            "_datajud_conflict_fields": [],
        }

    async def fake_load_steps(conn, *, version_id):
        return []

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert rag.CACHED_SOURCE_WARNING in context["source_warnings"]
    provider_process, _ = rag._provider_payload(context)
    assert rag.CACHED_SOURCE_WARNING in provider_process["source_warnings"]


def test_empty_steps_warning_must_be_inside_attention_section() -> None:
    context = {
        "code": "0000000-00.2026.8.21.0001",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "subjects": [],
        "parties": [],
        "secrecy_level": 0,
        "header": {},
        "step_count": 0,
        "steps": [],
        "source_warnings": [rag.EMPTY_STEPS_WARNING],
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
        "# Resumo do processo\n\n## Síntese\nNenhum movimento processual foi fornecido no payload.\n\n"
        "## Pontos de atenção\nNenhuma divergência factual identificada.",
        context,
    )
    assert invalid.passed is False
    assert (
        f"required attention fact missing: {rag.EMPTY_STEPS_WARNING}" in invalid.errors
    )

    valid = rag._validate_provider_summary(
        f"# Resumo do processo\n\n## Pontos de atenção\n{rag.EMPTY_STEPS_WARNING}",
        context,
    )
    assert valid.passed is True


@pytest.mark.asyncio
async def test_empty_steps_warning_is_sent_to_single_correction_attempt(monkeypatch) -> None:
    context = {
        "code": "0000000-00.2026.8.21.0001",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "subjects": [],
        "parties": [],
        "secrecy_level": 0,
        "header": {},
        "step_count": 0,
        "steps": [],
        "source_warnings": [rag.EMPTY_STEPS_WARNING],
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
    generation_errors: list[list[str] | None] = []
    persisted: dict = {}

    async def fake_existing(pool, process_id, version_id):
        return None

    async def fake_context(pool, process_id, version_id, *, tenant_id=None):
        assert tenant_id is None
        return context

    async def fake_generate(client, supplied_context, validation_errors=None):
        generation_errors.append(validation_errors)
        if validation_errors is None:
            return "# Resumo do processo\n\n## Pontos de atenção\nNenhuma divergência factual identificada."
        assert any(
            rag.EMPTY_STEPS_WARNING in error for error in validation_errors
        )
        return f"# Resumo do processo\n\n## Pontos de atenção\n{rag.EMPTY_STEPS_WARNING}"

    async def fake_persist(conn, **kwargs):
        persisted.update(kwargs)
        return True

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(rag, "_load_publishable_summary", fake_existing)
    monkeypatch.setattr(rag, "_load_context", fake_context)
    monkeypatch.setattr(rag, "anthropic_client", lambda api_key: object())
    monkeypatch.setattr(rag, "_generate", fake_generate)
    monkeypatch.setattr(rag, "_persist_summary", fake_persist)

    result = await rag.generate_summary(_Pool(), uuid4(), uuid4())

    assert len(generation_errors) == 2
    assert generation_errors[0] is None
    assert generation_errors[1]
    assert result["validation"] == {"passed": True, "errors": []}
    assert persisted["validation"]["passed"] is True
    assert rag.EMPTY_STEPS_WARNING in persisted["text"]
