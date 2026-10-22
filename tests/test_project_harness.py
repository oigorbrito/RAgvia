from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "project_harness.py"
SPEC = importlib.util.spec_from_file_location("project_harness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
project_harness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = project_harness
SPEC.loader.exec_module(project_harness)


def test_ci_gate_observations_cover_behavioral_and_operational_evidence() -> None:
    observations = project_harness._ci_gate_observations()
    by_name = {item.name: item for item in observations}

    expected = {
        "static-quality",
        "unit-tests",
        "synthetic-rag-eval",
        "offline-pipeline-eval",
        "offline-generation-eval",
        "frontend-behavior",
        "container-runtime-smoke",
        "backup-restore-drill",
        "postgres-integration",
        "offline-release-smoke",
    }

    assert expected <= by_name.keys()
    assert all(by_name[name].status == "pass" for name in expected)


def test_documentation_observations_require_empirical_contract_files() -> None:
    observations = project_harness._documentation_observations()

    assert observations
    assert all(item.status == "pass" for item in observations)


def test_guardrail_failure_is_observable_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Completed:
        returncode = 7
        stdout = "guardrail stdout"
        stderr = "guardrail stderr"

    monkeypatch.setattr(project_harness.subprocess, "run", lambda *args, **kwargs: Completed())

    observation = project_harness._run_guardrail(
        "example",
        "scripts/migration_harness.py",
    )

    assert observation.status == "fail"
    assert observation.kind == "guardrail"
    assert "guardrail stdout" in observation.detail
    assert "guardrail stderr" in observation.detail


def test_release_workflow_observations_cover_image_publish_evidence() -> None:
    observations = project_harness._release_workflow_observations()
    by_name = {item.name: item for item in observations}

    expected = {
        "image-project-harness",
        "image-unit-tests",
        "image-postgres-integration",
        "published-image-smoke",
        "published-image-attestation",
        "published-image-attestation-digest",
        "attestation-id-token-permission",
        "attestation-write-permission",
    }

    assert expected <= by_name.keys()
    assert all(by_name[name].status == "pass" for name in expected)
