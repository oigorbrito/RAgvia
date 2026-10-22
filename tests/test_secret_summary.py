from __future__ import annotations

import json
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


def _secret_context() -> dict:
    return {
        "code": "9999999-99.9999.9.99.9999",
        "class_name": "Procedimento Sigiloso",
        "secrecy_level": 1,
        "header": {
            "name": "NOME QUE NAO DEVE SER EXIBIDO",
            "instance": 1,
            "area": "Cível",
            "justice_description": "Justiça Estadual",
            "county": "Porto Alegre",
            "state": "RS",
            "city": "Porto Alegre",
            "amount": 999999.99,
        },
        "validation_parties": [
            {
                "name": "Pessoa Sigilosa Exemplo",
                "person_id": "1234567890",
            }
        ],
        "parties": [],
        "subjects": [],
        "steps": [],
    }


def test_secret_summary_is_minimal_and_deterministic() -> None:
    text = rag._secret_summary(_secret_context())

    assert "Procedimento Sigiloso" in text
    assert "Instância: 1" in text
    assert "Área: Cível" in text
    assert "Estado: RS" in text
    assert "restringidos por sigilo" in text
    assert "9999999-99.9999.9.99.9999" not in text
    assert "NOME QUE NAO DEVE SER EXIBIDO" not in text
    assert "999999.99" not in text
    assert "Pessoa Sigilosa Exemplo" not in text
    assert "1234567890" not in text


def test_secret_provider_payload_excludes_validation_only_party_metadata() -> None:
    context = _secret_context()

    provider_process, provider_steps = rag._provider_payload(context)
    rendered = json.dumps(provider_process, ensure_ascii=False)

    assert provider_steps == []
    assert "validation_parties" not in provider_process
    assert "parties" not in provider_process
    assert "Pessoa Sigilosa Exemplo" not in rendered
    assert "1234567890" not in rendered


@pytest.mark.asyncio
async def test_secret_context_retains_parties_only_for_local_validation(monkeypatch) -> None:
    secret_party = {"name": "Pessoa Sigilosa Exemplo", "person_id": "1234567890"}

    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "9999999-99.9999.9.99.9999",
            "court": "TJRS",
            "class_name": "Procedimento Sigiloso",
            "subjects": [{"name": "Assunto restrito"}],
            "parties": [secret_party],
            "secrecy_level": 1,
            "header": {"instance": 1},
        }

    monkeypatch.setattr(rag, "_load_process", fake_load_process)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert context["validation_parties"] == [secret_party]
    assert context["parties"] == []
    assert context["subjects"] == []
    assert context["steps"] == []
    provider_process, provider_steps = rag._provider_payload(context)
    assert provider_steps == []
    assert "validation_parties" not in provider_process


@pytest.mark.asyncio
async def test_secret_generation_does_not_require_or_call_anthropic(monkeypatch) -> None:
    context = _secret_context()
    persisted: dict = {}

    async def fake_load_publishable_summary(pool, process_id, version_id):
        return None

    async def fake_load_context(pool, process_id, version_id, *, tenant_id=None):
        assert tenant_id is None
        return context

    async def fake_persist_summary(conn, **kwargs):
        persisted.update(kwargs)
        return True

    def provider_must_not_be_created(api_key: str):
        raise AssertionError("Anthropic client must not be created for secret proceedings")

    monkeypatch.setattr(rag, "_load_publishable_summary", fake_load_publishable_summary)
    monkeypatch.setattr(rag, "_load_context", fake_load_context)
    monkeypatch.setattr(rag, "_persist_summary", fake_persist_summary)
    monkeypatch.setattr(rag, "anthropic_client", provider_must_not_be_created)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = await rag.generate_summary(_Pool(), uuid4(), uuid4())

    assert result["model"] == rag.RESTRICTED_MODEL
    assert result["validation"]["passed"] is True
    assert result["persisted"] is True
    assert persisted["model"] == rag.RESTRICTED_MODEL
    assert persisted["prompt_version"] == rag.RESTRICTED_PROMPT_VERSION
    assert persisted["text"] == rag._secret_summary(context)


@pytest.mark.asyncio
async def test_secret_generation_rejects_regression_that_leaks_party_name(monkeypatch) -> None:
    context = _secret_context()
    persisted: dict = {}

    async def fake_load_publishable_summary(pool, process_id, version_id):
        return None

    async def fake_load_context(pool, process_id, version_id, *, tenant_id=None):
        assert tenant_id is None
        return context

    async def fake_persist_summary(conn, **kwargs):
        persisted.update(kwargs)
        return True

    def leaking_secret_summary(_context):
        return "# Resumo do processo\n\nPessoa Sigilosa Exemplo consta nos autos."

    def provider_must_not_be_created(api_key: str):
        raise AssertionError("Anthropic client must not be created for secret proceedings")

    monkeypatch.setattr(rag, "_load_publishable_summary", fake_load_publishable_summary)
    monkeypatch.setattr(rag, "_load_context", fake_load_context)
    monkeypatch.setattr(rag, "_secret_summary", leaking_secret_summary)
    monkeypatch.setattr(rag, "_persist_summary", fake_persist_summary)
    monkeypatch.setattr(rag, "anthropic_client", provider_must_not_be_created)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = await rag.generate_summary(_Pool(), uuid4(), uuid4())

    assert result["model"] == rag.RESTRICTED_MODEL
    assert result["validation"]["passed"] is False
    assert "party names are prohibited for secret summary" in result["validation"]["errors"]
    assert persisted["validation"]["passed"] is False
