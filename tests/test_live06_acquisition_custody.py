from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "provider_live_smoke.py"
SPEC = importlib.util.spec_from_file_location("provider_live_smoke_live06", SOURCE)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)

CNJ = "0000000-00.2026.8.21.0001"
REQUEST_ID = "05ee9825-b2b4-480b-b29e-f071ca7d9c72"


@pytest.fixture
def authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "offline-test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setenv("JUDIT_REQUESTS_BASE_URL", "https://requests.prod.judit.io")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def test_one_post_and_get_only_resume_after_completed_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: None,
) -> None:
    calls: list[str] = []
    path = tmp_path / "private" / "judit-id"

    async def create(_code: str):
        calls.append("POST")
        assert path.with_name(path.name + ".attempt.json").is_file()
        return SimpleNamespace(request_id=REQUEST_ID)

    async def observe(value: str, *, post_calls: int):
        calls.append(f"GET:{post_calls}")
        assert value == REQUEST_ID
        assert path.read_text(encoding="utf-8") == REQUEST_ID + "\n"
        return {"status": "observed_completed", "post_calls": post_calls}

    monkeypatch.setattr(smoke, "create_lawsuit_request", create)
    monkeypatch.setattr(smoke, "_observe_judit_request", observe)
    first = asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    second = asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    assert calls == ["POST", "GET:1", "GET:0"]
    assert first["status"] == second["status"] == "roundtrip_completed"
    assert first["request_reused"] is False
    assert second["request_reused"] is True
    assert "request_id" not in first and REQUEST_ID not in json.dumps(first)
    assert REQUEST_ID not in json.dumps(second)
    marker = json.loads(path.with_name(path.name + ".attempt.json").read_text())
    assert marker["cnj_sha256"] == smoke._hash_identifier(CNJ)
    assert REQUEST_ID not in json.dumps(marker)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.with_name(path.name + ".attempt.json").stat().st_mode & 0o777 == 0o600


def test_timeout_after_post_blocks_duplicate_even_without_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: None,
) -> None:
    path = tmp_path / "judit-id"
    calls = 0

    async def create(_code: str):
        nonlocal calls
        calls += 1
        raise TimeoutError("unknown POST result")

    monkeypatch.setattr(smoke, "create_lawsuit_request", create)
    with pytest.raises(TimeoutError):
        asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    assert path.with_name(path.name + ".attempt.json").exists()
    assert not path.exists()
    with pytest.raises(RuntimeError, match="outcome unknown"):
        asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    assert calls == 1


def test_poll_failure_restarts_get_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: None,
) -> None:
    path = tmp_path / "judit-id"
    calls = []

    async def create(_code: str):
        calls.append("POST")
        return SimpleNamespace(request_id=REQUEST_ID)

    async def failing_observe(value: str, *, post_calls: int):
        assert value == REQUEST_ID and post_calls == 1
        raise RuntimeError("GET interrupted")

    async def resumed_observe(value: str, *, post_calls: int):
        calls.append("GET")
        assert value == REQUEST_ID and post_calls == 0
        return {"status": "observed_completed"}

    monkeypatch.setattr(smoke, "create_lawsuit_request", create)
    monkeypatch.setattr(smoke, "_observe_judit_request", failing_observe)
    with pytest.raises(RuntimeError, match="GET interrupted"):
        asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    assert path.read_text(encoding="utf-8") == REQUEST_ID + "\n"
    monkeypatch.setattr(smoke, "_observe_judit_request", resumed_observe)
    outcome = asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    assert calls == ["POST", "GET"]
    assert outcome["request_reused"] is True


def test_orphan_identifier_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: None,
) -> None:
    path = tmp_path / "judit-id"
    path.write_text("older-original-id\n", encoding="utf-8")

    async def forbidden(_code: str):
        raise AssertionError("must not POST")

    monkeypatch.setattr(smoke, "create_lawsuit_request", forbidden)
    with pytest.raises(RuntimeError, match="without acquisition marker"):
        asyncio.run(smoke._smoke_judit_roundtrip(CNJ, request_id_file=path))
    assert path.read_text(encoding="utf-8") == "older-original-id\n"


def test_marker_mismatch_or_uncertain_result_never_creates_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: None,
) -> None:
    path = tmp_path / "judit-id"
    assert smoke._prepare_judit_acquisition(path, CNJ) is None
    with pytest.raises(RuntimeError, match="outcome unknown"):
        smoke._prepare_judit_acquisition(path, CNJ)
    with pytest.raises(RuntimeError, match="does not match"):
        smoke._prepare_judit_acquisition(path, "1111111-11.2026.8.21.0001")
    monkeypatch.setenv("JUDIT_REQUESTS_BASE_URL", "https://requests.production.judit.io")
    with pytest.raises(RuntimeError, match="does not match"):
        smoke._prepare_judit_acquisition(path, CNJ)


@pytest.mark.parametrize("mode", ["judit", "roundtrip", "both"])
def test_cli_rejects_creation_without_custody(
    mode: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], authorized: None,
) -> None:
    argv = ["provider_live_smoke.py", "--cnj", CNJ]
    if mode == "roundtrip":
        argv.append("--judit-roundtrip")
    else:
        argv.extend(["--provider", mode])
    monkeypatch.setattr(sys, "argv", argv)

    async def forbidden(_code: str):
        raise AssertionError("must not POST")

    monkeypatch.setattr(smoke, "create_lawsuit_request", forbidden)
    assert smoke.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked_unsafe_acquisition_custody"


def test_actions_cannot_create_even_with_ephemeral_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], authorized: None,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(sys, "argv", [
        "provider_live_smoke.py", "--judit-roundtrip", "--cnj", CNJ,
        "--judit-request-id-file", str(tmp_path / "id"),
    ])
    assert smoke.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked_unsafe_acquisition_custody"
    assert not (tmp_path / "id.attempt.json").exists()


def test_get_only_observe_can_resume_from_private_id_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], authorized: None,
) -> None:
    path = tmp_path / "id"
    path.write_text(REQUEST_ID + "\n")
    calls = []

    async def fake_observe(value: str, *, post_calls: int):
        calls.append((value, post_calls))
        return {"status": "observed_completed", "request_id_sha256": smoke._hash_identifier(value)}

    monkeypatch.setattr(smoke, "_observe_judit_request", fake_observe)
    monkeypatch.setattr(sys, "argv", [
        "provider_live_smoke.py", "--judit-observe", "--judit-request-id-file", str(path),
    ])
    assert smoke.main() == 0
    report_json = capsys.readouterr().out
    assert REQUEST_ID not in report_json
    assert calls == [(REQUEST_ID, 0)]
