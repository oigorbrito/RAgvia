from uuid import uuid4

import pytest

import app.rag as rag
from app.retrieval import RankedStep, Step


class _ConnContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _ConnContext()


@pytest.mark.asyncio
async def test_secret_context_never_checks_or_loads_reranker(monkeypatch) -> None:
    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.0000.0.00.0000",
            "court": "TJ",
            "class_name": "Segredo",
            "subjects": ["sigiloso"],
            "parties": ["Parte"],
            "secrecy_level": 1,
            "header": {},
        }

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(
        rag,
        "configured_reranker_scorer",
        lambda: (_ for _ in ()).throw(AssertionError("reranker config must not be read")),
    )

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert context["secrecy_level"] == 1
    assert context["steps"] == []


@pytest.mark.asyncio
async def test_short_process_never_constructs_reranker(monkeypatch) -> None:
    steps = [Step(id=uuid4(), step_number=i, text=f"movimento {i}") for i in range(1, 41)]

    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.0000.0.00.0000",
            "court": "TJ",
            "class_name": "Procedimento",
            "subjects": [],
            "parties": [],
            "secrecy_level": 0,
            "header": {},
        }

    async def fake_load_steps(conn, *, version_id):
        return steps

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)
    monkeypatch.setattr(
        rag,
        "configured_reranker_scorer",
        lambda: (_ for _ in ()).throw(AssertionError("short process must not construct reranker")),
    )

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert len(context["steps"]) == 40


@pytest.mark.asyncio
async def test_long_process_enabled_uses_configured_scorer(monkeypatch) -> None:
    steps = [Step(id=uuid4(), step_number=i, text=f"movimento {i}") for i in range(1, 61)]
    calls = {}
    scorer = object()

    async def fake_load_process(pool, process_id, version_id):
        return {
            "code": "0000000-00.0000.0.00.0000",
            "court": "TJ",
            "class_name": "Procedimento",
            "subjects": [],
            "parties": [],
            "secrecy_level": 0,
            "header": {},
        }

    async def fake_load_steps(conn, *, version_id):
        return steps

    async def fake_select_context_steps(
        *, query, steps, vector_scores, scorer, lexical_scores=None
    ):
        calls["scorer"] = scorer
        calls["step_count"] = len(steps)
        return [RankedStep(step=step, score=1.0) for step in steps[:15]]

    monkeypatch.setattr(rag, "_load_process", fake_load_process)
    monkeypatch.setattr(rag, "load_steps", fake_load_steps)
    monkeypatch.setattr(rag, "vector_retrieval_configured", lambda: False)
    monkeypatch.setattr(rag, "configured_reranker_scorer", lambda: scorer)
    monkeypatch.setattr(rag, "select_context_steps", fake_select_context_steps)

    context = await rag._load_context(_Pool(), uuid4(), uuid4())

    assert calls["scorer"] is scorer
    assert calls["step_count"] == 60
    assert len(context["steps"]) == 15
