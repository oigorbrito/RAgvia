from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_offline_pipeline.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_offline_pipeline", _EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EVALUATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVALUATOR)


def test_offline_pipeline_probe_matches_versioned_baseline() -> None:
    report = _EVALUATOR.evaluate_pipeline(_EVALUATOR.load_dataset())
    baseline = _EVALUATOR.load_baseline()

    assert report["cases"] == 30
    assert report["counts"] == baseline["counts"]
    assert _EVALUATOR.compare_to_baseline(report, baseline) == []
    assert report["metrics"]["mandatory_milestone_recall"] == pytest.approx(1.0)
    assert report["metrics"]["policy_candidate_precision"] == pytest.approx(634 / 673)
    assert report["metrics"]["source_presence_rate"] == pytest.approx(1.0)
    assert report["metrics"]["secret_summary_leakage_rate"] == pytest.approx(0.0)


def test_probe_exercises_real_short_and_long_retrieval_paths() -> None:
    report = _EVALUATOR.evaluate_pipeline(_EVALUATOR.load_dataset())

    assert report["case_metrics"]["synthetic-01"]["selected"] == 12
    assert report["case_metrics"]["synthetic-05"]["selected"] == 20
    assert report["case_metrics"]["synthetic-30"]["selected"] == 20


def test_secret_cases_use_local_boundary_and_do_not_leak_markers() -> None:
    report = _EVALUATOR.evaluate_pipeline(_EVALUATOR.load_dataset())

    for case_id in ("synthetic-04", "synthetic-19"):
        case_report = report["case_metrics"][case_id]
        assert case_report["mode"] == "secret-local"
        assert case_report["secret_checks"] == 4
        assert case_report["secret_leaks"] == []


def test_offline_pipeline_probe_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_connect(*args, **kwargs):
        raise AssertionError("offline pipeline probe attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    report = _EVALUATOR.evaluate_pipeline(_EVALUATOR.load_dataset())
    assert report["cases"] == 30


def test_pipeline_regression_violates_baseline() -> None:
    report = _EVALUATOR.evaluate_pipeline(_EVALUATOR.load_dataset())
    baseline = _EVALUATOR.load_baseline()
    report["metrics"]["mandatory_milestone_recall"] = 0.5

    failures = _EVALUATOR.compare_to_baseline(report, baseline)
    assert failures == [
        {
            "metric": "mandatory_milestone_recall",
            "direction": "min",
            "threshold": 1.0,
            "actual": 0.5,
        }
    ]
