from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import rag
from app.tasks import PermanentTaskError


def _ranked(texts: list[str]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            step=SimpleNamespace(
                id=uuid4(),
                step_number=index + 1,
                occurred_at=None,
                title=f"Movimento {index + 1}",
                text=text,
            )
        )
        for index, text in enumerate(texts)
    ]


def test_step_serialization_caps_each_step_and_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_PROMPT_MAX_CHARS", "200")
    monkeypatch.setenv("PROVIDER_STEP_TEXT_MAX_CHARS", "30")
    monkeypatch.setenv("PROVIDER_STEPS_TEXT_MAX_CHARS", "40")

    serialized = rag._serialize_steps(_ranked(["A" * 100, "B" * 100]))

    assert len(serialized) == 2
    assert sum(len(step["text"]) for step in serialized) <= 40
    assert all(len(step["text"]) <= 30 for step in serialized)
    assert {step["step_number"] for step in serialized} == {1, 2}


def test_invalid_context_limit_relationship_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_PROMPT_MAX_CHARS", "100")
    monkeypatch.setenv("PROVIDER_STEP_TEXT_MAX_CHARS", "80")
    monkeypatch.setenv("PROVIDER_STEPS_TEXT_MAX_CHARS", "70")

    with pytest.raises(RuntimeError, match="STEP_TEXT_MAX_CHARS"):
        rag.provider_context_limits()


class _NeverCalledMessages:
    async def create(self, **kwargs):
        raise AssertionError("provider must not be called when prompt exceeds hard cap")


class _NeverCalledClient:
    messages = _NeverCalledMessages()


@pytest.mark.asyncio
async def test_oversized_final_prompt_fails_permanently_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_PROMPT_MAX_CHARS", "200")
    monkeypatch.setenv("PROVIDER_STEP_TEXT_MAX_CHARS", "20")
    monkeypatch.setenv("PROVIDER_STEPS_TEXT_MAX_CHARS", "40")

    context = {
        "code": "0000000-00.0000.0.00.0000",
        "court": "TJRS",
        "class_name": "X" * 500,
        "subjects": [],
        "parties": [],
        "secrecy_level": 0,
        "header": {},
        "steps": [],
    }

    with pytest.raises(PermanentTaskError, match="provider prompt exceeds"):
        await rag._generate(_NeverCalledClient(), context)
