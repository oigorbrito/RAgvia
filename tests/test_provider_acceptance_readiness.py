from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "provider_acceptance_readiness.py"
SPEC = importlib.util.spec_from_file_location("provider_acceptance_readiness", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load provider acceptance readiness module")
readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = readiness
SPEC.loader.exec_module(readiness)


def test_repository_provider_acceptance_is_armed_without_credentials() -> None:
    report = readiness.readiness_report()

    if not report["passed"]:
        raise AssertionError(f"provider readiness errors: {report['errors']!r}")
    if report["network_calls_performed"] is not False:
        raise AssertionError("credential-free readiness must not perform network calls")
    if report["secrets_required_for_this_check"] is not False:
        raise AssertionError("credential-free readiness must not require secrets")

    expected_states = {
        "judit": "ready_to_provision",
        "anthropic": "ready_to_provision",
        "datajud": "disabled_pending_authorization",
        "cohere": "disabled_pending_authorization",
        "bge": "artifact_evidence_pending",
    }
    actual_states = {
        provider: details["state"]
        for provider, details in report["providers"].items()
    }
    if actual_states != expected_states:
        raise AssertionError(
            f"unexpected provider readiness states: {actual_states!r}; "
            f"expected {expected_states!r}"
        )


def test_rejects_accidentally_populated_provider_secret(tmp_path: Path) -> None:
    env_text = readiness.DEFAULT_ENV.read_text(encoding="utf-8")
    env_path = tmp_path / ".env.production.example"
    env_path.write_text(
        env_text.replace(
            "JUDIT_API_KEY=replace-with-judit-api-key",
            "JUDIT_API_KEY=accidentally-populated-value",
        ),
        encoding="utf-8",
    )

    report = readiness.readiness_report(env_path=env_path)

    if report["passed"]:
        raise AssertionError("populated provider credential must fail readiness")
    if not any("JUDIT_API_KEY" in error for error in report["errors"]):
        raise AssertionError(f"expected JUDIT_API_KEY error, got {report['errors']!r}")


def test_rejects_external_gate_enabled_in_repository_template(tmp_path: Path) -> None:
    env_text = readiness.DEFAULT_ENV.read_text(encoding="utf-8")
    env_path = tmp_path / ".env.production.example"
    env_path.write_text(
        env_text.replace("DATAJUD_ENABLED=false", "DATAJUD_ENABLED=true"),
        encoding="utf-8",
    )

    report = readiness.readiness_report(env_path=env_path)

    if report["passed"]:
        raise AssertionError("enabled external gate must fail credential-free readiness")
    expected = "production template must keep DATAJUD_ENABLED=false before online acceptance"
    if expected not in report["errors"]:
        raise AssertionError(f"expected {expected!r}, got {report['errors']!r}")
