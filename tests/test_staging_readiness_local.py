from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "staging_readiness_local.py"
_SPEC = importlib.util.spec_from_file_location("staging_readiness_local", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = module
_SPEC.loader.exec_module(module)


def test_base_origin_requires_bare_https() -> None:
    assert module._base_origin("https://staging.example.test") == "https://staging.example.test"
    for invalid in (
        "http://staging.example.test",
        "https://user@example.test",
        "https://staging.example.test/path",
        "https://staging.example.test?x=1",
    ):
        with pytest.raises(ValueError, match="bare HTTPS origin"):
            module._base_origin(invalid)


def test_run_probe_requires_health_ready_and_invalid_webhook_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = {
        "/health": 200,
        "/ready": 200,
        "/webhooks/judit/rpy-readiness-invalid-token": 404,
    }

    def fake_request(origin: str, path: str, *, method: str = "GET"):
        assert origin == "https://staging.example.test"
        assert method == ("POST" if path.startswith("/webhooks/") else "GET")
        return module.ProbeResult(path=path, status=statuses[path])

    monkeypatch.setattr(module, "_request", fake_request)
    results = module.run_probe("https://staging.example.test")
    assert [result.status for result in results] == [200, 200, 404]


def test_run_probe_fails_when_ingress_cannot_reach_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request(origin: str, path: str, *, method: str = "GET"):
        status = 502 if path == "/ready" else (404 if method == "POST" else 200)
        return module.ProbeResult(path=path, status=status)

    monkeypatch.setattr(module, "_request", fake_request)
    with pytest.raises(RuntimeError, match="/ready: expected 200, got 502"):
        module.run_probe("https://staging.example.test")
