from __future__ import annotations

import importlib.util
import re
import socket
from pathlib import Path

import pytest

_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_synthetic_rag.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_synthetic_rag", _EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EVALUATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVALUATOR)
evaluate = _EVALUATOR.evaluate
load_dataset = _EVALUATOR.load_dataset

REQUIRED_DOMAINS = {"civil", "trabalhista", "criminal", "execucao_fiscal"}
REQUIRED_TAGS = {
    "primeira_instancia",
    "segunda_instancia",
    "baixado",
    "segredo_justica",
    "mais_200_movimentos",
    "steps_vazio",
    "inconsistencia_area",
    "inconsistencia_tags",
    "inconsistencia_classe",
    "inconsistencia_fase",
    "inconsistencia_comarca",
    "inconsistencia_rito",
    "inconsistencia_gratuidade",
    "anexo_pendente",
    "evento_inconsistente",
}


def test_dataset_has_minimum_size_and_required_coverage() -> None:
    cases = load_dataset()
    assert len(cases) == 30
    assert {case["domain"] for case in cases} == REQUIRED_DOMAINS
    tags = {tag for case in cases for tag in case["tags"]}
    assert REQUIRED_TAGS.issubset(tags)
    assert any(case["instance"] == 2 for case in cases)
    assert any(case["status"] == "baixado" for case in cases)
    assert any(case["secrecy_level"] > 0 for case in cases)
    assert any(case["step_count"] > 200 for case in cases)
    assert any(case["step_count"] == 0 for case in cases)


def test_dataset_contains_no_obvious_personal_identifiers() -> None:
    rendered = str(load_dataset())
    assert not re.search(r"(?<!\d)\d{11,14}(?!\d)", rendered)
    assert "@" not in rendered
    assert "cpf" not in rendered.casefold()
    assert "cnpj" not in rendered.casefold()


def test_report_is_reproducible_and_exposes_required_metrics() -> None:
    report = evaluate(load_dataset())
    assert report["cases"] == 30
    assert report["counts"] == {
        "claims": 60,
        "unsupported_claims": 3,
        "expected_milestones": 30,
        "recovered_milestones": 27,
        "expected_inconsistencies": 18,
        "detected_inconsistencies": 17,
        "predicted_attention": 19,
        "false_positive_attention": 1,
    }
    assert report["metrics"]["unsupported_assertion_rate"] == pytest.approx(0.05)
    assert report["metrics"]["milestone_recall"] == pytest.approx(0.9)
    assert report["metrics"]["inconsistency_detection_recall"] == pytest.approx(17 / 18)
    assert report["metrics"]["attention_false_positive_rate"] == pytest.approx(1 / 19)


def test_evaluator_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_connect(*args, **kwargs):
        raise AssertionError("synthetic evaluator attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    report = evaluate(load_dataset())
    assert report["cases"] == 30
