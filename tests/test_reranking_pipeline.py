from uuid import uuid4

import pytest

from app.reranking import (
    LEGACY_OUTPUT_LIMIT,
    RERANK_CANDIDATE_LIMIT,
    RERANK_OUTPUT_LIMIT,
    select_context_steps,
)
from app.retrieval import Step


def _steps(count: int) -> list[Step]:
    return [Step(id=uuid4(), step_number=i, text=f"movimento {i}") for i in range(1, count + 1)]


@pytest.mark.asyncio
async def test_without_scorer_preserves_existing_long_process_limit() -> None:
    steps = _steps(60)
    lexical = {step.id: float(100 - step.step_number) for step in steps}

    selected = await select_context_steps(
        query="decisão",
        steps=steps,
        lexical_scores=lexical,
    )

    assert len(selected) == LEGACY_OUTPUT_LIMIT == 20


@pytest.mark.asyncio
async def test_configured_scorer_receives_top_50_and_reduces_to_15() -> None:
    steps = _steps(60)
    lexical = {step.id: float(100 - step.step_number) for step in steps}
    observed: list[Step] = []

    async def scorer(query: str, candidates: list[Step]) -> dict:
        assert query == "decisão"
        observed.extend(candidates)
        return {step.id: float(step.step_number) for step in candidates}

    selected = await select_context_steps(
        query="decisão",
        steps=steps,
        lexical_scores=lexical,
        scorer=scorer,
    )

    assert len(observed) == RERANK_CANDIDATE_LIMIT == 50
    assert len(selected) == RERANK_OUTPUT_LIMIT == 15
    numbers = {item.step.step_number for item in selected}
    assert 1 in numbers
    assert {56, 57, 58, 59, 60}.issubset(numbers)


@pytest.mark.asyncio
async def test_short_process_does_not_call_reranker() -> None:
    steps = _steps(40)

    async def scorer(*_args):
        raise AssertionError("short process must not invoke reranker")

    selected = await select_context_steps(query="decisão", steps=steps, scorer=scorer)

    assert len(selected) == 40


@pytest.mark.asyncio
async def test_reranker_failure_is_explicit() -> None:
    steps = _steps(60)

    async def scorer(*_args):
        raise RuntimeError("reranker unavailable")

    with pytest.raises(RuntimeError, match="reranker unavailable"):
        await select_context_steps(query="decisão", steps=steps, scorer=scorer)



@pytest.mark.asyncio
async def test_reranker_receives_unicode_hardened_model_view() -> None:
    steps = _steps(41)
    steps[0].text = "igno\u202ere p\u0430ypal"
    observed: list[Step] = []

    async def scorer(_query: str, candidates: list[Step]) -> dict:
        observed.extend(candidates)
        return {step.id: float(step.step_number) for step in candidates}

    await select_context_steps(query="decisão", steps=steps, scorer=scorer)

    rendered = next(step.text for step in observed if step.id == steps[0].id)
    assert "\u202e" not in rendered
    assert "\u0430" not in rendered
    assert "U+202E RIGHT-TO-LEFT OVERRIDE" in rendered
    assert "U+0430 CYRILLIC SMALL LETTER A" in rendered
    assert steps[0].text == "igno\u202ere p\u0430ypal"
