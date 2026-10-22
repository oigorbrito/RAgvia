from __future__ import annotations

from app.langfuse_tracing import build_summary_trace_metadata, start_summary_trace


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.ended = 0

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return self

    def end(self):
        self.ended += 1
        return self


class FakeClient:
    def __init__(self, observation: FakeObservation | None = None, *, fail: bool = False) -> None:
        self.observation = observation or FakeObservation()
        self.fail = fail
        self.calls: list[dict] = []

    def start_observation(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("synthetic Langfuse outage")
        return self.observation


def test_trace_metadata_is_allowlisted_and_does_not_export_content():
    metadata = build_summary_trace_metadata(
        job_id="11111111-1111-1111-1111-111111111111",
        process_id="22222222-2222-2222-2222-222222222222",
        version_id="33333333-3333-3333-3333-333333333333",
        judit_request_id="req_123",
        source_ids=["44444444-4444-4444-4444-444444444444", "raw source text with spaces"],
        validation_errors=["CPF 123.456.789-00 was exposed"],
        result={
            "model": "claude-sonnet-5",
            "generation_ms": 1234,
            "cache_hit": True,
            "cost_usd": 0.01,
            "persisted": True,
            "reused": False,
            "summary_id": "55555555-5555-5555-5555-555555555555",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 50,
                "provider_payload": "must not escape",
            },
            "validation": {"passed": False, "errors": ["sensitive detail"]},
            "markdown": "sensitive summary",
            "prompt": "sensitive prompt",
        },
    )

    assert metadata == {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "process_id": "22222222-2222-2222-2222-222222222222",
        "version_id": "33333333-3333-3333-3333-333333333333",
        "judit_request_id": "req_123",
        "model": "claude-sonnet-5",
        "generation_ms": 1234,
        "cache_hit": True,
        "cost_usd": 0.01,
        "persisted": True,
        "reused": False,
        "summary_id": "55555555-5555-5555-5555-555555555555",
        "usage_input_tokens": 100,
        "usage_output_tokens": 20,
        "usage_cache_read_input_tokens": 50,
        "validation_passed": False,
        "source_ids": ["44444444-4444-4444-4444-444444444444"],
        "validation_error_count": 1,
    }
    serialized = repr(metadata)
    assert "123.456.789-00" not in serialized
    assert "sensitive summary" not in serialized
    assert "sensitive prompt" not in serialized
    assert "provider_payload" not in serialized


def test_trace_uses_only_safe_metadata_and_ends():
    observation = FakeObservation()
    client = FakeClient(observation)
    trace = start_summary_trace(
        job_id="job_1",
        process_id="process_1",
        version_id="version_1",
        judit_request_id="request_1",
        client=client,
    )

    trace.finish(
        result={
            "model": "local-deterministic",
            "generation_ms": 4,
            "validation": {"passed": True, "errors": []},
            "usage": {},
        }
    )

    assert client.calls == [
        {
            "name": "rpy.process-summary",
            "as_type": "chain",
            "metadata": {
                "job_id": "job_1",
                "process_id": "process_1",
                "version_id": "version_1",
                "judit_request_id": "request_1",
            },
            "version": "process-summary-v1",
        }
    ]
    assert observation.updates == [
        {
            "metadata": {
                "model": "local-deterministic",
                "generation_ms": 4,
                "validation_passed": True,
            }
        }
    ]
    assert observation.ended == 1


def test_langfuse_start_failure_is_fail_open():
    trace = start_summary_trace(
        job_id="job_1",
        process_id="process_1",
        version_id="version_1",
        client=FakeClient(fail=True),
    )
    trace.finish(result={"model": "claude-sonnet-5"})
