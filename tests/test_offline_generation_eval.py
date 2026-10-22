from __future__ import annotations

import asyncio
import copy
import importlib.util
import socket
import sys
from pathlib import Path

import pytest

_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_offline_generation.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_offline_generation", _EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EVALUATOR = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _EVALUATOR
_SPEC.loader.exec_module(_EVALUATOR)


def test_offline_generation_matches_versioned_baseline() -> None:
    report = asyncio.run(_EVALUATOR.evaluate_generation(_EVALUATOR.load_dataset()))
    baseline = _EVALUATOR.load_baseline()
    assert _EVALUATOR.compare_to_baseline(report, baseline) == []
    assert report["counts"]["forced_retries"] >= 1
    assert report["counts"]["forced_retries"] == report["counts"]["recovered_retries"]
    assert report["counts"]["secret_provider_calls"] == 0
    verification_counts = report["counts"]["claim_verification"]
    assert sum(verification_counts.values()) == report["counts"]["material_claims"]
    assert set(verification_counts) == {
        "supported",
        "contradicted",
        "insufficient",
        "not_evaluated",
    }
    assert 0.0 <= report["metrics"]["deterministic_supported_claim_rate"] <= 1.0
    assert 0.0 <= report["metrics"]["semantic_unverified_claim_rate"] <= 1.0
    assert (
        report["metrics"]["deterministic_supported_claim_rate"]
        + report["metrics"]["semantic_unverified_claim_rate"]
        == pytest.approx(1.0)
    )
    verification_by_class = report["counts"]["claim_verification_by_class"]
    assert verification_by_class
    assert sum(
        sum(class_counts.values())
        for class_counts in verification_by_class.values()
    ) == report["counts"]["material_claims"]
    assert verification_by_class["current_status"]["not_evaluated"] > 0
    assert verification_by_class["attention"]["not_evaluated"] > 0


def test_generation_regression_is_reported() -> None:
    report = asyncio.run(_EVALUATOR.evaluate_generation(_EVALUATOR.load_dataset()))
    baseline = copy.deepcopy(_EVALUATOR.load_baseline())
    report["metrics"]["final_validation_pass_rate"] = 0.5
    failures = _EVALUATOR.compare_to_baseline(report, baseline)
    assert failures == [
        {
            "metric": "final_validation_pass_rate",
            "direction": "min",
            "threshold": 1.0,
            "actual": 0.5,
        }
    ]


def test_offline_generation_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def blocked_connect(self, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return original_connect(self, address, *args, **kwargs)
        raise AssertionError(f"offline generation evaluation attempted network access to {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    report = asyncio.run(_EVALUATOR.evaluate_generation(_EVALUATOR.load_dataset()))
    assert report["metrics"]["final_validation_pass_rate"] == 1.0
    assert report["metrics"]["secret_provider_call_rate"] == 0.0

def test_blocked_connect_blocks_external_ips(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def blocked_connect(self, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return original_connect(self, address, *args, **kwargs)
        raise AssertionError(f"offline generation evaluation attempted network access to {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    
    s = socket.socket()
    try:
        with pytest.raises(AssertionError, match="offline generation evaluation attempted network access to"):
            s.connect(("8.8.8.8", 53))
    finally:
        s.close()
