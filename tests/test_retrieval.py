from uuid import uuid4

import pytest

from app.retrieval import Step, bm25_scores, rank_steps


def _step(number: int, text: str, *, title: str | None = None) -> Step:
    return Step(id=uuid4(), step_number=number, text=text, title=title)


def test_bm25_prefers_exact_terms() -> None:
    relevant = _step(1, "CITAÇÃO do requerido realizada")
    other = _step(2, "Petição juntada aos autos")
    scores = bm25_scores("citação requerido", [relevant, other])
    assert scores[relevant.id] > scores[other.id]


def test_short_process_returns_every_step() -> None:
    steps = [_step(i, f"movimento {i}") for i in range(1, 21)]
    ranked = rank_steps(query="sentença", steps=steps, limit=5)
    assert [item.step.step_number for item in ranked] == list(range(1, 21))


def test_long_process_forces_first_last_recent_and_milestone() -> None:
    steps = [_step(i, f"movimento comum {i}") for i in range(1, 51)]
    steps[9] = _step(10, "SENTENÇA julgando o mérito")
    ranked = rank_steps(query="termo inexistente", steps=steps, limit=8)
    numbers = {item.step.step_number for item in ranked}
    assert 1 in numbers
    assert 50 in numbers
    assert {46, 47, 48, 49, 50}.issubset(numbers)
    assert 10 in numbers


@pytest.mark.parametrize(
    "milestone",
    [
        "sentença",
        "acórdão",
        "liminar",
        "tutela",
        "citação",
        "audiência",
        "trânsito em julgado",
        "arquivamento",
        "extinção",
        "perícia",
        "penhora",
        "baixa definitiva",
        "recurso",
        "apelação",
        "embargos",
    ],
)
def test_long_process_forces_documented_milestones(milestone: str) -> None:
    steps = [_step(i, f"movimento neutro {i}") for i in range(1, 51)]
    target = _step(20, "conteúdo ordinário", title=milestone)
    steps[19] = target

    ranked = rank_steps(query="termo impossível", steps=steps, limit=7)
    by_id = {item.step.id: item for item in ranked}

    assert target.id in by_id
    assert by_id[target.id].forced is True


def test_recency_boost_favors_later_equal_hits() -> None:
    steps = [_step(i, "sem relação") for i in range(1, 41)]
    early = _step(42, "penhora realizada")
    late = _step(50, "penhora realizada")
    steps.extend([early, late])
    ranked = rank_steps(query="penhora", steps=steps, limit=10)
    scores = {item.step.id: item.score for item in ranked}
    assert scores[late.id] > scores[early.id]


def test_vector_signal_contributes_half_of_base_score() -> None:
    steps = [_step(i, "movimento neutro") for i in range(1, 51)]
    semantic_hit = steps[20]
    vector_scores = {semantic_hit.id: 1.0}
    ranked = rank_steps(
        query="termo ausente",
        steps=steps,
        vector_scores=vector_scores,
        limit=10,
    )
    by_id = {item.step.id: item for item in ranked}
    assert semantic_hit.id in by_id
    assert by_id[semantic_hit.id].vector == 1.0
    assert by_id[semantic_hit.id].score > 0.5


def test_postgres_lexical_scores_cannot_override_bm25_final_signal() -> None:
    steps = [_step(i, "movimento neutro") for i in range(1, 51)]
    bm25_hit = _step(20, "tutela decisão tutela decisão")
    sql_rank_hit = _step(21, "conteúdo sem relação")
    steps[19] = bm25_hit
    steps[20] = sql_rank_hit

    ranked = rank_steps(
        query="tutela decisão",
        steps=steps,
        lexical_scores={sql_rank_hit.id: 999.0},
        limit=10,
    )
    by_id = {item.step.id: item for item in ranked}

    assert bm25_hit.id in by_id
    assert by_id[bm25_hit.id].lexical == by_id[bm25_hit.id].bm25
    assert by_id[bm25_hit.id].lexical > 0.0
    assert by_id.get(sql_rank_hit.id) is None or by_id[sql_rank_hit.id].lexical == 0.0


def test_hybrid_base_score_uses_equal_bm25_and_vector_weights() -> None:
    steps = [_step(i, "movimento neutro") for i in range(1, 51)]
    target = _step(25, "tutela")
    steps[24] = target

    ranked = rank_steps(
        query="tutela",
        steps=steps,
        vector_scores={target.id: 1.0},
        limit=10,
    )
    item = next(candidate for candidate in ranked if candidate.step.id == target.id)
    recency = 1.0 + 0.3 * (target.step_number / 50)

    assert item.lexical == 1.0
    assert item.vector == 1.0
    assert item.score / recency == pytest.approx(1.0)
