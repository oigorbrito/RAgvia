from __future__ import annotations

import pytest

from scripts.evaluate_paired_legal_corpus import evaluate_case, frozen_case
from scripts.evaluate_writing_parity import DESTINATION


CASE = "m01-doc01-scheduled-hearing"


def test_one_authentic_pair_and_human_review_remains_pending() -> None:
    assert frozen_case(CASE)["donor_commit"] == "0d0f8cbb495292a3f3d61fba75428f3136059ea8"
    text = DESTINATION.read_text(encoding="utf-8")
    result = evaluate_case(case_id=CASE, document=text)
    assert result["comparison"]["deterministic_preservation_passed"]
    assert result["comparison"]["total"] == 13
    assert result["claim_source_metadata"]["state"] == "not_supplied"
    assert not result["live_model_evaluated"]
    assert not result["legal_quality_qualified"]
    assert all(row["score_0_to_3"] is None for row in result["human_review"].values())
    assert "synthetic" not in result  # No raw process narratives in report.


def test_true_omission_cannot_pass_deterministic_preservation() -> None:
    text = DESTINATION.read_text(encoding="utf-8")
    reduced = text.replace("Pessoa Sintética Alfa", "Parte omitida")
    result = evaluate_case(case_id=CASE, document=reduced)
    assert not result["comparison"]["deterministic_preservation_passed"]
    assert "Partes e polos explicitamente cadastrados" in result["comparison"]["unresolved"]


def test_adverse_held_hearing_is_flagged_without_claiming_nli() -> None:
    text = DESTINATION.read_text(encoding="utf-8")
    result = evaluate_case(case_id=CASE, document=text + "\nA audiência foi realizada.\n")
    assert "hearing_only_scheduled_but_claimed_held" in result["narrow_risk_signals"]
    assert result["legal_quality_qualified"] is False


def test_legal_explanation_omission_remains_visible_separately() -> None:
    text = DESTINATION.read_text(encoding="utf-8")
    phrase = "VI - a efetiva prevenção e reparação"
    assert phrase in text
    result = evaluate_case(case_id=CASE, document=text.replace(phrase, "lacuna"))
    assert "Explicações de classe/assunto sustentadas por conteúdo recuperado" in result["comparison"]["unresolved"]
    assert result["legal_quality_qualified"] is False


def test_claim_metadata_does_not_establish_legal_entailment() -> None:
    text = DESTINATION.read_text(encoding="utf-8")
    result = evaluate_case(
        case_id=CASE, document=text,
        claims=[
            {"verification_status": "supported", "sources": [{"evidence_ref": "p-123", "source_kind": "process"}]},
            {"verification_status": "supported", "sources": []},
        ],
    )
    meta = result["claim_source_metadata"]
    assert (meta["total"], meta["supported"], meta["without_valid_source_reference"]) == (2, 2, 1)
    assert meta["semantic_entailment_proven"] is False


def test_unknown_case_is_not_invented_for_benchmark() -> None:
    with pytest.raises(ValueError, match="donor reference"):
        evaluate_case(case_id="synthetic-02", document="Resumo sintético")
