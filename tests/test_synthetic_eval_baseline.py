from __future__ import annotations

import copy
import importlib.util
import socket
from pathlib import Path

import pytest

_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_synthetic_rag.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_synthetic_rag_baseline", _EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EVALUATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVALUATOR)


def test_current_synthetic_report_matches_versioned_baseline() -> None:
    report = _EVALUATOR.evaluate(_EVALUATOR.load_dataset())
    baseline = _EVALUATOR.load_baseline()

    assert _EVALUATOR.compare_to_baseline(report, baseline) == []
    assert len(report["case_metrics"]) == 30


def test_case_regression_identifies_case_and_metric() -> None:
    report = _EVALUATOR.evaluate(_EVALUATOR.load_dataset())
    baseline = _EVALUATOR.load_baseline()
    report = copy.deepcopy(report)
    report["case_metrics"]["synthetic-01"]["unsupported_assertion_rate"] = 0.5

    failures = _EVALUATOR.compare_to_baseline(report, baseline)

    case_failures = [failure for failure in failures if failure.get("scope") == "case"]
    assert any(
        failure.get("case_id") == "synthetic-01"
        and failure.get("metric") == "unsupported_assertion_rate"
        for failure in case_failures
    )


def test_aggregate_regression_is_reported() -> None:
    report = _EVALUATOR.evaluate(_EVALUATOR.load_dataset())
    baseline = _EVALUATOR.load_baseline()
    report = copy.deepcopy(report)
    report["metrics"]["milestone_recall"] = 0.5

    failures = _EVALUATOR.compare_to_baseline(report, baseline)

    assert any(
        failure.get("scope") == "aggregate"
        and failure.get("metric") == "milestone_recall"
        for failure in failures
    )


def test_dataset_change_requires_explicit_baseline_update(tmp_path: Path) -> None:
    report = _EVALUATOR.evaluate(_EVALUATOR.load_dataset())
    baseline = _EVALUATOR.load_baseline()
    changed_dataset = tmp_path / "synthetic_cases.json"
    changed_dataset.write_bytes(_EVALUATOR.DEFAULT_DATASET.read_bytes() + b"\n")

    failures = _EVALUATOR.compare_to_baseline(
        report,
        baseline,
        dataset_path=changed_dataset,
    )

    assert any(
        failure.get("scope") == "dataset"
        and failure.get("metric") == "git_blob_sha"
        for failure in failures
    )


def test_runtime_configuration_change_requires_baseline_update() -> None:
    report = _EVALUATOR.evaluate(_EVALUATOR.load_dataset())
    baseline = copy.deepcopy(_EVALUATOR.load_baseline())
    baseline["runtime"]["prompt_version"] = "stale-prompt-version"

    failures = _EVALUATOR.compare_to_baseline(report, baseline)

    assert any(
        failure.get("scope") == "runtime"
        and failure.get("metric") == "configuration"
        for failure in failures
    )


def test_baseline_check_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_connect(*args, **kwargs):
        raise AssertionError("synthetic baseline gate attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    report = _EVALUATOR.evaluate(_EVALUATOR.load_dataset())
    failures = _EVALUATOR.compare_to_baseline(report, _EVALUATOR.load_baseline())
    assert failures == []
