from __future__ import annotations

from pathlib import Path

import pytest

from app.providers import anthropic_model
from app.rag import OPUS_MODEL, SONNET_MODEL, _generation_model
from tests.test_anthropic_contract import _Client, _context
from app.rag import _generate


@pytest.mark.parametrize("step_count,expected", [(100, SONNET_MODEL), (101, OPUS_MODEL)])
def test_unconfigured_model_preserves_volume_routing(monkeypatch, step_count, expected):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert _generation_model({"step_count": step_count}) == expected


@pytest.mark.parametrize("value", [" ", " model ", "model\n", "x" * 129, "https://not-a-model"])
def test_invalid_model_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv("ANTHROPIC_MODEL", value)
    with pytest.raises(ValueError, match="bounded model identifier"):
        anthropic_model(SONNET_MODEL)


@pytest.mark.asyncio
async def test_explicit_model_reaches_request_and_telemetry_without_contract_changes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-acceptance-fixture")
    for count in (3, 101):
        client = _Client()
        context = _context(step_count=count)
        await _generate(client, context)
        request = client.messages.calls[0]
        assert request["model"] == "claude-acceptance-fixture"
        assert context["_generation_telemetry"]["model"] == request["model"]
        assert request["max_tokens"] == 4000
        assert request["output_config"]["format"]["schema"]["additionalProperties"] is False
        assert "tools" not in request


def test_local_compose_anthropic_secret_is_not_in_shared_api_environment():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    api_environment = compose.split("environment: &rpy-env", 1)[1].split("    ports:", 1)[0]
    assert "ANTHROPIC_API_KEY" not in api_environment
    assert "ANTHROPIC_MODEL" not in api_environment
    for name in ("worker-1", "worker-2"):
        block = compose.split(f"  {name}:", 1)[1].split("    depends_on:", 1)[0]
        assert "ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}" in block
        assert "ANTHROPIC_MODEL: ${ANTHROPIC_MODEL:-}" in block
