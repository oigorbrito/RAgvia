from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from app.retrieval import RankedStep, Step, rank_steps, rerank_steps
from app.unicode_security import model_view_text

RERANK_CANDIDATE_LIMIT = 50
RERANK_OUTPUT_LIMIT = 15
LEGACY_OUTPUT_LIMIT = 20

RerankerScorer = Callable[[str, Sequence[Step]], Awaitable[dict[UUID, float]]]


async def select_context_steps(
    *,
    query: str,
    steps: Sequence[Step],
    lexical_scores: dict[UUID, float] | None = None,
    vector_scores: dict[UUID, float] | None = None,
    scorer: RerankerScorer | None = None,
) -> list[RankedStep]:
    """Select context movements with an optional post-retrieval reranker.

    Without a scorer this preserves the existing retrieval contract. With a
    scorer, long-process retrieval expands the hybrid candidate window to 50,
    asks the scorer only about those already-filtered candidates, and reduces
    the context to approximately 15 while preserving mandatory movements.

    Scorer failures are intentionally not swallowed. A configured reranker
    must fail explicitly instead of silently changing retrieval semantics.
    """
    candidate_limit = RERANK_CANDIDATE_LIMIT if scorer is not None else LEGACY_OUTPUT_LIMIT
    candidates = rank_steps(
        query=query,
        steps=steps,
        lexical_scores=lexical_scores,
        vector_scores=vector_scores,
        limit=candidate_limit,
    )
    if scorer is None or len(steps) <= 40:
        return candidates

    candidate_steps = [candidate.step for candidate in candidates]
    model_steps = [
        Step(
            id=step.id,
            step_number=step.step_number,
            text=model_view_text(step.text).text,
            title=(
                model_view_text(step.title).text
                if step.title is not None
                else None
            ),
            occurred_at=step.occurred_at,
            source_step_number=step.source_step_number,
        )
        for step in candidate_steps
    ]
    scores = await scorer(model_view_text(query).text, model_steps)
    selected_steps = rerank_steps(
        steps=candidate_steps,
        scores=scores,
        limit=RERANK_OUTPUT_LIMIT,
    )
    by_id = {candidate.step.id: candidate for candidate in candidates}
    return [by_id[step.id] for step in selected_steps]
