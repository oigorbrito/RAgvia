"""FUNC05 regression contracts for literal RagResumo alerts.

The donor's alerts are lexical observations, not authority to alter process data.
This test keeps the original safe boundary while fallback publication is reviewed.
"""
from __future__ import annotations

import json

import pytest
from pathlib import Path

from app.judit import extract_promotable_fields
from app.rag import _provider_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "judit" / "parser_parity_synthetic.json"


def test_donor_cadastro_alert_does_not_rewrite_source_or_provider_context() -> None:
    original = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = extract_promotable_fields(original)
    source_parser = result["header"]["source_parser"]
    assert source_parser["indicators"], "The donor synthetic fixture must exercise actual alerts"
    assert all(indicator["kind"] == "heuristic" for indicator in source_parser["indicators"])
    assert source_parser["semantics"] == "lexical_annotations_not_verified_facts"

    # The suspect original area is retained as registered; lexical advice must
    # never overwrite the authoritative field or become an LLM instruction.
    assert result["header"]["area"] == "DIREITO DO TRABALHO"
    process, movements = _provider_payload(result)
    provider_text = json.dumps([process, movements], ensure_ascii=False, default=str)
    assert "source_parser" not in provider_text
    assert "lexical_annotations_not_verified_facts" not in provider_text
    for indicator in source_parser["indicators"]:
        assert indicator["message"] not in provider_text


def test_suspect_secondary_class_is_annotation_not_canonical_class() -> None:
    result = extract_promotable_fields(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    classifications = result["header"]["source_parser"]["classifications"]
    suspects = [item for item in classifications if item["suspect"]]
    assert suspects, "Fixture must contain a donor-flagged class"
    assert result["class_name"] == classifications[0]["name"]
    assert all(item["name"] != result["class_name"] for item in suspects)

@pytest.mark.asyncio
async def test_public_summary_never_uses_donor_template_without_provider(monkeypatch) -> None:
    from uuid import uuid4

    import app.rag as rag

    async def no_existing_summary(pool, process_id, version_id):
        return None

    async def public_context(pool, process_id, version_id, *, tenant_id=None):
        return {
            "code": "0000000-00.2026.8.21.0001",
            "court": "TJRS",
            "class_name": "Procedimento Comum",
            "secrecy_level": 0,
            "parties": [],
            "steps": [],
            "header": {},
        }

    async def must_not_persist(*args, **kwargs):
        raise AssertionError("No summary may be persisted without the provider")

    class Pool:
        def acquire(self):
            raise AssertionError("Pool must not be acquired for publication")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(rag, "_load_publishable_summary", no_existing_summary)
    monkeypatch.setattr(rag, "_load_context", public_context)
    monkeypatch.setattr(rag, "_persist_summary", must_not_persist)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is required"):
        await rag.generate_summary(Pool(), uuid4(), uuid4())


@pytest.mark.asyncio
async def test_public_provider_failure_cannot_trigger_template_publication(monkeypatch) -> None:
    from uuid import uuid4

    import app.rag as rag

    async def no_existing_summary(pool, process_id, version_id):
        return None

    async def public_context(pool, process_id, version_id, *, tenant_id=None):
        return {
            "code": "0000000-00.2026.8.21.0001",
            "court": "TJRS",
            "class_name": "Procedimento Comum",
            "secrecy_level": 0,
            "parties": [],
            "steps": [],
            "header": {},
        }

    class Client:
        async def close(self):
            pass

    async def provider_unavailable(client, context, validation_errors=None):
        raise RuntimeError("synthetic provider failure")

    async def must_not_persist(*args, **kwargs):
        raise AssertionError("Provider failure must not publish a template")

    class Pool:
        def acquire(self):
            raise AssertionError("Pool must not be acquired for publication")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(rag, "_load_publishable_summary", no_existing_summary)
    monkeypatch.setattr(rag, "_load_context", public_context)
    monkeypatch.setattr(rag, "anthropic_client", lambda *args, **kwargs: Client())
    monkeypatch.setattr(rag, "_generate", provider_unavailable)
    monkeypatch.setattr(rag, "_persist_summary", must_not_persist)

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        await rag.generate_summary(Pool(), uuid4(), uuid4())
