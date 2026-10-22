from __future__ import annotations

import asyncio
import os
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "provider_live_smoke.py"
SPEC = importlib.util.spec_from_file_location("provider_live_smoke", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load provider live smoke module")
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def test_judit_missing_credentials_skips_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_create(code: str):
        nonlocal called
        called = True
        return SimpleNamespace(request_id="should-not-run")

    monkeypatch.delenv("JUDIT_API_KEY", raising=False)
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)

    report = asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))

    if report["status"] != "skipped_missing_credentials":
        raise AssertionError(f"unexpected report: {report!r}")
    if report["network_calls_performed"] is not False or called:
        raise AssertionError("missing credentials must not perform Judit network work")


def test_judit_live_path_uses_existing_adapter_and_hashes_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    async def fake_create(code: str):
        observed.append(code)
        return SimpleNamespace(request_id="provider-request-123")

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)

    report = asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))

    if report["status"] != "request_created":
        raise AssertionError(f"unexpected report: {report!r}")
    if observed != ["0000000-00.2026.8.21.0001"]:
        raise AssertionError(f"unexpected adapter input: {observed!r}")
    if "request_id" in report:
        raise AssertionError("raw provider request id must not be emitted")
    if len(report["request_id_sha256"]) != 64:
        raise AssertionError(f"request id hash is invalid: {report!r}")


def test_judit_live_path_rejects_paid_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")

    with pytest.raises(RuntimeError, match="JUDIT_ATTACHMENTS_ENABLED=false"):
        asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))


def test_datajud_requires_explicit_authorization_before_adapter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_lookup(**kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(status="ok", metadata=object(), error_code=None)

    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "false")
    monkeypatch.setenv("DATAJUD_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "lookup_datajud_metadata", fake_lookup)

    report = asyncio.run(smoke.run_smoke("datajud", "0000000-00.2026.8.21.0001"))

    if report["status"] != "skipped_missing_authorization":
        raise AssertionError(f"unexpected report: {report!r}")
    if called:
        raise AssertionError("unauthorized DataJud smoke must not call the adapter")


def test_datajud_live_path_uses_existing_adapter_and_sanitized_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    async def fake_lookup(**kwargs):
        observed.append(kwargs)
        return SimpleNamespace(status="ok", metadata=object(), error_code=None)

    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.setenv("DATAJUD_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "lookup_datajud_metadata", fake_lookup)

    report = asyncio.run(smoke.run_smoke("datajud", "0000000-00.2026.8.21.0001"))

    if report["status"] != "ok" or report["metadata_present"] is not True:
        raise AssertionError(f"unexpected report: {report!r}")
    expected = {"code": "0000000-00.2026.8.21.0001", "secrecy_level": 0}
    if observed != [expected]:
        raise AssertionError(f"unexpected adapter invocation: {observed!r}")


def test_empty_boolean_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "")
    if smoke._env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False) is not False:
        raise AssertionError("empty boolean environment value must use the supplied default")


def test_judit_missing_provider_authorization_skips_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_create(code: str):
        nonlocal called
        called = True
        return SimpleNamespace(request_id="should-not-run")

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "false")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)

    report = asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))

    if report["status"] != "skipped_missing_authorization":
        raise AssertionError(f"unexpected report: {report!r}")
    if called:
        raise AssertionError("unauthorized Judit smoke must not call the adapter")


def test_main_missing_cnj_emits_json_skip_and_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["provider_live_smoke.py", "--provider", "judit", "--cnj", ""],
    )

    exit_code = smoke.main()
    report = __import__("json").loads(capsys.readouterr().out)

    if exit_code != 0:
        raise AssertionError(f"missing CNJ should be an inert skip, got {exit_code}")
    if report["status"] != "skipped_missing_cnj":
        raise AssertionError(f"unexpected report: {report!r}")
    if report["network_calls_performed"] is not False:
        raise AssertionError("missing CNJ must not perform network calls")


def test_main_provider_exception_emits_sanitized_json_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run_smoke(provider: str, code: str):
        raise RuntimeError("provider body with secret-value")

    monkeypatch.setattr(smoke, "run_smoke", fake_run_smoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provider_live_smoke.py",
            "--provider",
            "judit",
            "--cnj",
            "0000000-00.2026.8.21.0001",
        ],
    )

    exit_code = smoke.main()
    rendered = capsys.readouterr().out
    report = __import__("json").loads(rendered)

    if exit_code != 1:
        raise AssertionError(f"provider exception must fail the smoke, got {exit_code}")
    if report["status"] != "error" or report["error_class"] != "RuntimeError":
        raise AssertionError(f"unexpected report: {report!r}")
    if "secret-value" in rendered:
        raise AssertionError("provider exception text must not leak into machine-readable output")


def test_judit_smoke_can_persist_request_id_without_exposing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = "provider-request-secret-123"
    request_id_file = tmp_path / "private" / "judit-request-id"

    async def fake_create(_code: str):
        return SimpleNamespace(request_id=request_id)

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)

    report = asyncio.run(
        smoke._smoke_judit(
            "0000000-00.2026.8.21.0001",
            request_id_file=request_id_file,
        )
    )

    assert report["status"] == "request_created"
    assert report["request_id_persisted"] is True
    assert report["request_id_sha256"] == smoke._hash_identifier(request_id)
    assert "request_id" not in report
    assert request_id_file.read_text(encoding="utf-8") == request_id + "\n"
    if os.name != "nt":
        assert request_id_file.stat().st_mode & 0o777 == 0o600


def test_judit_roundtrip_persists_request_id_before_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id_file = tmp_path / "judit-request-id"

    async def fake_create(_code: str):
        return SimpleNamespace(request_id="provider-request-123")

    async def fail_observe(_request_id: str, *, post_calls: int):
        assert request_id_file.read_text(encoding="utf-8") == "provider-request-123\n"
        assert post_calls == 1
        raise RuntimeError("observation failed")

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)
    monkeypatch.setattr(smoke, "_observe_judit_request", fail_observe)

    with pytest.raises(RuntimeError, match="observation failed"):
        asyncio.run(
            smoke._smoke_judit_roundtrip(
                "0000000-00.2026.8.21.0001",
                request_id_file=request_id_file,
            )
        )

    if os.name != "nt":
        assert request_id_file.stat().st_mode & 0o777 == 0o600


def test_both_preflight_requires_all_credentials_before_any_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    judit_called = False
    datajud_called = False

    async def fake_create(code: str):
        nonlocal judit_called
        judit_called = True
        return SimpleNamespace(request_id="should-not-run")

    async def fake_lookup(**kwargs):
        nonlocal datajud_called
        datajud_called = True
        return SimpleNamespace(status="ok", metadata=object(), error_code=None)

    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)
    monkeypatch.setattr(smoke, "lookup_datajud_metadata", fake_lookup)

    report = asyncio.run(smoke.run_smoke("both", "0000000-00.2026.8.21.0001"))

    if report["status"] != "skipped_missing_credentials":
        raise AssertionError(f"unexpected report: {report!r}")
    if report["network_calls_performed"] is not False:
        raise AssertionError("combined preflight must perform no partial network calls")
    if judit_called or datajud_called:
        raise AssertionError("combined preflight must block both providers when either is not ready")


def test_both_live_path_runs_judit_and_datajud_in_same_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    async def fake_create(code: str):
        observed.append(f"judit:{code}")
        return SimpleNamespace(request_id="provider-request-123")

    async def fake_lookup(**kwargs):
        observed.append(f"datajud:{kwargs['code']}")
        return SimpleNamespace(status="ok", metadata=object(), error_code=None)

    async def fake_diagnostic():
        return {
            "provider": "judit",
            "executed": True,
            "network_calls_performed": True,
            "network_call_type": "non_creating_connectivity_check",
            "status": "ok",
            "latency_ms": 1.0,
        }

    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("DATAJUD_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)
    monkeypatch.setattr(smoke, "lookup_datajud_metadata", fake_lookup)
    monkeypatch.setattr(smoke, "diagnose_judit", fake_diagnostic)

    report = asyncio.run(smoke.run_smoke("both", "0000000-00.2026.8.21.0001"))

    if report["status"] != "ok" or report["executed"] is not True:
        raise AssertionError(f"unexpected report: {report!r}")
    if report["network_calls_performed"] is not True:
        raise AssertionError("combined acceptance must record both provider network calls")
    if set(observed) != {
        "judit:0000000-00.2026.8.21.0001",
        "datajud:0000000-00.2026.8.21.0001",
    }:
        raise AssertionError(f"unexpected combined provider calls: {observed!r}")
    if report["results"]["judit"]["status"] != "request_created":
        raise AssertionError(f"unexpected Judit result: {report!r}")
    if report["results"]["datajud"]["status"] != "ok":
        raise AssertionError(f"unexpected DataJud result: {report!r}")


def test_capture_redacts_cnj_and_can_be_replayed_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_path = tmp_path / "provider-smoke-capture.json"
    code = "0000000-00.2026.8.21.0001"
    report = {
        "provider": "both",
        "executed": True,
        "network_calls_performed": True,
        "status": "ok",
        "results": {
            "judit": {
                "provider": "judit",
                "executed": True,
                "network_calls_performed": True,
                "status": "request_created",
                "latency_ms": 12.3,
                "request_id_sha256": "a" * 64,
            },
            "datajud": {
                "provider": "datajud",
                "executed": True,
                "network_calls_performed": True,
                "status": "ok",
                "latency_ms": 8.2,
                "metadata_present": True,
                "error_code": None,
            },
        },
    }

    smoke.write_capture(capture_path, code=code, report=report)
    rendered = capture_path.read_text(encoding="utf-8")

    if code in rendered:
        raise AssertionError("raw CNJ must not be persisted in provider capture")
    document = __import__("json").loads(rendered)
    if document["request"]["cnj_sha256"] != smoke._hash_identifier(code):
        raise AssertionError(f"unexpected capture identity: {document!r}")

    replay = smoke.load_replay(capture_path, code=code)
    if replay["status"] != "ok" or replay["replayed"] is not True:
        raise AssertionError(f"unexpected replay: {replay!r}")
    if replay["network_calls_performed"] is not False:
        raise AssertionError("replay must never claim provider network calls")


def test_replay_rejects_capture_for_different_cnj(tmp_path: Path) -> None:
    capture_path = tmp_path / "provider-smoke-capture.json"
    smoke.write_capture(
        capture_path,
        code="0000000-00.2026.8.21.0001",
        report={
            "provider": "both",
            "executed": True,
            "network_calls_performed": True,
            "status": "ok",
        },
    )

    with pytest.raises(RuntimeError, match="does not match"):
        smoke.load_replay(capture_path, code="1111111-11.2026.8.21.0001")


def test_main_replay_does_not_require_provider_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = "0000000-00.2026.8.21.0001"
    capture_path = tmp_path / "provider-smoke-capture.json"
    smoke.write_capture(
        capture_path,
        code=code,
        report={
            "provider": "both",
            "executed": True,
            "network_calls_performed": True,
            "status": "ok",
            "results": {},
        },
    )
    monkeypatch.delenv("JUDIT_API_KEY", raising=False)
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provider_live_smoke.py",
            "--provider",
            "both",
            "--cnj",
            code,
            "--replay-file",
            str(capture_path),
        ],
    )

    exit_code = smoke.main()
    report = __import__("json").loads(capsys.readouterr().out)

    if exit_code != 0:
        raise AssertionError(f"replay should succeed without provider credentials, got {exit_code}")
    if report["replayed"] is not True or report["network_calls_performed"] is not False:
        raise AssertionError(f"unexpected replay report: {report!r}")


def test_judit_diagnostic_reports_safe_http_error_without_paid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail():
        raise smoke.JuditRequestError(
            "safe message",
            error_code="http_403",
            http_status=403,
            retry_safe=True,
        )

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "check_judit_connectivity", fail)

    report = asyncio.run(smoke.diagnose_judit())

    assert report["status"] == "error"
    assert report["error_code"] == "http_403"
    assert report["http_status"] == 403
    assert report["network_call_type"] == "non_creating_reachability_probe"


def test_judit_diagnostic_treats_documented_bad_request_as_reachable_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail():
        raise smoke.JuditRequestError(
            "safe message",
            error_code="http_400",
            http_status=400,
            retry_safe=True,
            provider_error_code="HttpBadRequestError",
        )

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "check_judit_connectivity", fail)

    report = asyncio.run(smoke.diagnose_judit())

    assert report["status"] == "reachable_unverified"
    assert report["auth_validated"] is False
    assert report["network_calls_performed"] is True


def test_both_blocks_paid_calls_when_judit_diagnostic_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    judit_called = False
    datajud_called = False

    async def diagnostic():
        return {
            "provider": "judit",
            "executed": True,
            "network_calls_performed": True,
            "network_call_type": "non_creating_connectivity_check",
            "status": "error",
            "error_code": "http_401",
            "http_status": 401,
        }

    async def fake_judit(code: str):
        nonlocal judit_called
        judit_called = True
        return {}

    async def fake_datajud(code: str):
        nonlocal datajud_called
        datajud_called = True
        return {}

    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("DATAJUD_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "diagnose_judit", diagnostic)
    monkeypatch.setattr(smoke, "_smoke_judit", fake_judit)
    monkeypatch.setattr(smoke, "_smoke_datajud", fake_datajud)

    report = asyncio.run(smoke.run_smoke("both", "0000000-00.2026.8.21.0001"))

    assert report["status"] == "blocked_judit_diagnostic"
    assert report["network_calls_performed"] is False
    assert judit_called is False
    assert datajud_called is False


def test_cnj_preflight_accepts_valid_canonical_without_network() -> None:
    report = smoke.preflight_cnj("0000000-30.2026.8.21.0001")

    assert report == {
        "provider": "local",
        "executed": True,
        "network_calls_performed": False,
        "status": "ok",
        "format_valid": True,
        "checksum_valid": True,
        "digit_count": 20,
        "input_form": "canonical",
        "justice_code": "8",
        "tribunal_code": "21",
    }


def test_cnj_preflight_accepts_valid_digits_without_network() -> None:
    report = smoke.preflight_cnj("00000003020268210001")

    assert report["status"] == "ok"
    assert report["input_form"] == "digits"
    assert report["format_valid"] is True
    assert report["checksum_valid"] is True
    assert report["network_calls_performed"] is False


def test_cnj_preflight_rejects_bad_checksum_without_network() -> None:
    report = smoke.preflight_cnj("0000000-00.2026.8.21.0001")

    assert report["status"] == "invalid_checksum"
    assert report["format_valid"] is True
    assert report["checksum_valid"] is False
    assert report["network_calls_performed"] is False


def test_cnj_preflight_rejects_bad_format_without_echoing_value() -> None:
    raw = "not-a-cnj-123"
    report = smoke.preflight_cnj(raw)

    assert report["status"] == "invalid_cnj"
    assert report["format_valid"] is False
    assert report["network_calls_performed"] is False
    assert raw not in __import__("json").dumps(report)


def test_main_cnj_preflight_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("provider call must not occur during preflight")

    monkeypatch.setattr(smoke, "run_smoke", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provider_live_smoke.py",
            "--preflight-cnj",
            "--cnj",
            "0000000-30.2026.8.21.0001",
        ],
    )

    exit_code = smoke.main()
    report = __import__("json").loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["network_calls_performed"] is False



def test_observe_existing_judit_request_never_creates_provider_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = False
    statuses = []
    responses = []

    async def forbidden_create(_code: str):
        nonlocal created
        created = True
        raise AssertionError("observe mode must not create a Judit request")

    async def fake_status(request_id: str):
        statuses.append(request_id)
        return SimpleNamespace(status="completed")

    async def fake_responses(request_id: str):
        responses.append(request_id)
        return SimpleNamespace(
            request_status="completed",
            response_count=1,
            lawsuit_response_count=1,
            application_info_count=0,
            application_error_count=0,
            application_error_code=None,
            application_error_message=None,
            other_response_count=0,
            direct_payload_count=0,
        )

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "create_lawsuit_request", forbidden_create)
    monkeypatch.setattr(smoke, "get_lawsuit_request_status", fake_status)
    monkeypatch.setattr(smoke, "get_lawsuit_responses", fake_responses)

    report = asyncio.run(
        smoke._observe_judit_request("provider-request-existing", post_calls=0)
    )

    assert report["status"] == "observed_completed"
    assert report["post_calls"] == 0
    assert report["get_calls"] == 2
    assert "request_id" not in report
    assert len(report["request_id_sha256"]) == 64
    assert statuses == ["provider-request-existing"]
    assert responses == ["provider-request-existing"]
    assert created is False


def test_observe_judit_reports_payload_received_while_provider_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_status(_request_id: str):
        return SimpleNamespace(status="pending")

    async def fake_responses(_request_id: str):
        return SimpleNamespace(
            request_status="pending",
            response_count=2,
            lawsuit_response_count=2,
            application_info_count=0,
            application_error_count=0,
            application_error_code=None,
            application_error_message=None,
            other_response_count=0,
            direct_payload_count=0,
        )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "get_lawsuit_request_status", fake_status)
    monkeypatch.setattr(smoke, "get_lawsuit_responses", fake_responses)
    monkeypatch.setattr(smoke.asyncio, "sleep", no_sleep)

    report = asyncio.run(
        smoke._observe_judit_request("provider-request-existing", post_calls=1)
    )

    assert report["status"] == "observed_payload_received_pending_completion"
    assert report["effective_request_status"] == "pending"
    assert report["lawsuit_response_count"] == 2
    assert report["application_error_count"] == 0
    assert report["post_calls"] == 1
    assert report["status_poll_attempts"] == 40


def test_judit_roundtrip_preserves_pending_payload_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create(_code: str):
        return SimpleNamespace(request_id="provider-request-123")

    async def fake_observe(_request_id: str, *, post_calls: int):
        assert post_calls == 1
        return {
            "provider": "judit",
            "executed": True,
            "network_calls_performed": True,
            "status": "observed_payload_received_pending_completion",
            "lawsuit_response_count": 2,
        }

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)
    monkeypatch.setattr(smoke, "_observe_judit_request", fake_observe)

    report = asyncio.run(smoke._smoke_judit_roundtrip("0000000-00.2026.8.21.0001"))

    assert report["status"] == "roundtrip_payload_received_pending_completion"
    assert report["lawsuit_response_count"] == 2


def test_observe_existing_judit_request_requires_explicit_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "false")

    report = asyncio.run(
        smoke._observe_judit_request("provider-request-existing", post_calls=0)
    )

    assert report["status"] == "skipped_missing_authorization"
    assert report["network_calls_performed"] is False


def test_main_judit_observe_does_not_require_cnj(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_observe(request_id: str, *, post_calls: int):
        assert request_id == "existing-request"
        assert post_calls == 0
        return {
            "provider": "judit",
            "executed": True,
            "network_calls_performed": True,
            "status": "observed_completed",
            "post_calls": 0,
        }

    monkeypatch.setattr(smoke, "_observe_judit_request", fake_observe)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provider_live_smoke.py",
            "--judit-observe",
            "--request-id",
            "existing-request",
            "--cnj",
            "",
        ],
    )

    exit_code = smoke.main()
    report = __import__("json").loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "observed_completed"
    assert report["post_calls"] == 0
