import json
from uuid import uuid4

import pytest

import app.reranker_cohere as cohere
from app.retrieval import Step


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self._payload if amount < 0 else self._payload[:amount]


@pytest.mark.asyncio
async def test_cohere_reranker_maps_indices_to_step_ids(monkeypatch) -> None:
    steps = [
        Step(id=uuid4(), step_number=1, text="primeiro"),
        Step(id=uuid4(), step_number=2, text="segundo"),
    ]
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            }
        )

    monkeypatch.setenv("COHERE_API_KEY", "unit-test-value")
    monkeypatch.setenv("PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setattr(cohere, "urlopen", fake_urlopen)

    scores = await cohere.CohereRerankerScorer()(query="decisao relevante", steps=steps)

    assert scores == {steps[1].id: 0.9, steps[0].id: 0.2}
    assert captured["url"] == "https://api.cohere.com/v2/rerank"
    assert captured["authorization"] == "Bearer unit-test-value"
    assert captured["body"]["model"] == "rerank-v4.0-pro"
    assert captured["body"]["query"] == "decisao relevante"
    assert captured["body"]["top_n"] == 2
    assert captured["body"]["documents"] == ["primeiro", "segundo"]


@pytest.mark.asyncio
async def test_cohere_reranker_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("COHERE_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="COHERE_API_KEY is required"):
        await cohere.CohereRerankerScorer()(
            query="consulta",
            steps=[Step(id=uuid4(), step_number=1, text="movimento")],
        )


def test_cohere_reranker_rejects_missing_or_duplicate_results() -> None:
    steps = [
        Step(id=uuid4(), step_number=1, text="a"),
        Step(id=uuid4(), step_number=2, text="b"),
    ]

    with pytest.raises(RuntimeError, match="1 results for 2 candidates"):
        cohere._decode_scores(
            {"results": [{"index": 0, "relevance_score": 1.0}]},
            steps=steps,
        )

    with pytest.raises(RuntimeError, match="invalid index"):
        cohere._decode_scores(
            {
                "results": [
                    {"index": 0, "relevance_score": 1.0},
                    {"index": 0, "relevance_score": 0.5},
                ]
            },
            steps=steps,
        )


def test_cohere_reranker_rejects_oversized_response(monkeypatch) -> None:
    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, amount: int = -1) -> bytes:
            if amount != cohere.COHERE_RERANK_MAX_RESPONSE_BYTES + 1:
                raise AssertionError(f"unexpected read bound: {amount}")
            return b"x" * amount

    monkeypatch.setattr(cohere, "urlopen", lambda request, timeout: OversizedResponse())
    with pytest.raises(cohere.CohereRerankerError, match="exceeded safe size"):
        cohere._post_rerank_sync(
            api_key="synthetic-key",
            model="rerank-v4.0-pro",
            query="consulta",
            documents=["a", "b"],
            timeout_seconds=1,
        )


class _RawRerankResponse:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"results":[],"results":[]}',
        b'{"results":[{"index":0,"relevance_score":NaN}]}',
    ],
)
def test_cohere_reranker_rejects_ambiguous_provider_json(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    monkeypatch.setattr(
        cohere,
        "urlopen",
        lambda request, timeout: _RawRerankResponse(raw),
    )
    with pytest.raises(RuntimeError, match="not valid JSON"):
        cohere._post_rerank_sync(
            api_key="synthetic-key",
            model="rerank-v4.0-pro",
            query="consulta",
            documents=["documento"],
            timeout_seconds=1,
        )
