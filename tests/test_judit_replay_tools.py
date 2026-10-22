from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load("export_judit_replay", "scripts/export_judit_replay.py")
replayer = _load("replay_judit_webhooks", "scripts/replay_judit_webhooks.py")


def _lawsuit_payload() -> dict:
    return {
        "callback_id": "callback-real-123",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": "request-real-123",
        "payload": {
            "request_id": "request-real-123",
            "response_id": "response-real-123",
            "response_type": "lawsuit",
            "response_data": {
                "code": "0000000-00.2026.8.21.0001",
                "parties": [
                    {
                        "name": "Pessoa Real",
                        "main_document": "12345678901",
                        "email": "person@example.test",
                    }
                ],
                "steps": [
                    {
                        "step_type": "DECISAO",
                        "content": "Texto processual de teste.",
                    }
                ],
            },
            "tags": {"cached_response": False},
        },
    }


def test_sanitize_delivery_replaces_identifiers_and_direct_pii() -> None:
    code = "0000000-00.2026.8.21.0001"
    raw = _lawsuit_payload()

    sanitized = exporter.sanitize_delivery(raw, code=code)
    rendered = json.dumps(sanitized, ensure_ascii=False)

    if "callback-real-123" in rendered or "request-real-123" in rendered:
        raise AssertionError("raw provider identifiers must not survive sanitized export")
    if "response-real-123" in rendered:
        raise AssertionError("raw response id must not survive sanitized export")
    if "Pessoa Real" in rendered or "12345678901" in rendered:
        raise AssertionError("direct party PII must not survive sanitized export")
    if "person@example.test" in rendered:
        raise AssertionError("email must not survive sanitized export")
    if sanitized["payload"]["response_data"]["code"] != code:
        raise AssertionError("canonical CNJ must remain available inside private replay bundle")


def test_load_bundle_rejects_different_cnj(tmp_path: Path) -> None:
    code = "0000000-00.2026.8.21.0001"
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "judit_webhook_replay_bundle",
                "cnj_sha256": replayer._hash(code),
                "deliveries": [
                    {
                        "received_order": 1,
                        "event_type": "response_created",
                        "payload": exporter.sanitize_delivery(_lawsuit_payload(), code=code),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="does not match"):
        replayer.load_bundle(path, code="1111111-11.2026.8.21.0001")


def test_replay_posts_each_delivery_without_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[tuple[str, bytes]] = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit: int):
            return b'{"ok":true}'

    def fake_urlopen(request, timeout):
        observed.append((request.full_url, request.data))
        return FakeResponse()

    monkeypatch.setattr(replayer.urllib.request, "urlopen", fake_urlopen)
    bundle = {
        "deliveries": [
            {
                "received_order": 1,
                "event_type": "response_created",
                "payload": exporter.sanitize_delivery(
                    _lawsuit_payload(), code="0000000-00.2026.8.21.0001"
                ),
            },
            {
                "received_order": 2,
                "event_type": "request_completed",
                "payload": {
                    "callback_id": "completion-test",
                    "event_type": "request_completed",
                    "reference_type": "request",
                    "reference_id": "request-test",
                    "payload": {"status": "completed"},
                },
            },
        ]
    }

    results = replayer.replay_bundle(
        bundle=bundle,
        base_url="http://rpy.test",
        webhook_token="secret-token",
    )

    if len(results) != 2 or len(observed) != 2:
        raise AssertionError(f"unexpected replay result: {results!r}, {observed!r}")
    if not all(url == "http://rpy.test/webhooks/judit/secret-token" for url, _ in observed):
        raise AssertionError(f"unexpected replay target: {observed!r}")
