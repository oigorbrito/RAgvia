from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app import rag
from app.validation import ValidationResult


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def acquire(self):
        return _Acquire()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "one_shot",
        "regeneration_allowed",
        "expected_calls",
        "expected_validation",
    ),
    [
        (True, False, 1, False),
        (True, True, 1, False),
        (False, False, 1, False),
        (False, True, 2, True),
    ],
)
async def test_iasummary_retry_obeys_one_shot_budget(
    monkeypatch,
    one_shot,
    regeneration_allowed,
    expected_calls,
    expected_validation,
):
    """A failed first summary cannot cause an unauthorized second provider call."""
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", str(one_shot).lower())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-only")

    process_id, version_id, tenant_id = (UUID(int=n) for n in (801, 802, 803))
    calls = []

    async def no_existing(*_args):
        return None

    async def load_context(*_args, **_kwargs):
        return {
            "code": "0000000-00.2024.8.21.0001",
            "secrecy_level": 0,
            "step_count": 1,
            "_selected_sources": [],
            "_attachment_sources": [],
            "_glossary_sources": [],
            "_unicode_security_flags": [],
        }

    async def fake_generate(_client, _context, errors=None):
        calls.append(errors)
        return "invalid" if len(calls) == 1 else "corrected"

    def fake_validate(text, _context):
        return ValidationResult(passed=(text == "corrected"), errors=[] if text == "corrected" else ["invalid"])

    async def fake_persist(_conn, **kwargs):
        assert kwargs["validation"]["passed"] is expected_validation
        assert kwargs["text"] == ("corrected" if expected_calls == 2 else "invalid")
        return True

    async def close():
        pass

    monkeypatch.setattr(rag, "_load_publishable_summary", no_existing)
    monkeypatch.setattr(rag, "_load_context", load_context)
    monkeypatch.setattr(rag, "_generate", fake_generate)
    monkeypatch.setattr(rag, "_validate_provider_summary", fake_validate)
    monkeypatch.setattr(rag, "_persist_summary", fake_persist)
    monkeypatch.setattr(
        rag, "anthropic_client",
        lambda *_args, **_kwargs: SimpleNamespace(
            validation_regeneration_allowed=regeneration_allowed,
            close=close,
        ),
    )

    result = await rag.generate_summary(
        _Pool(), process_id, version_id, tenant_id=tenant_id
    )

    assert len(calls) == expected_calls
    assert result["validation"]["passed"] is expected_validation
