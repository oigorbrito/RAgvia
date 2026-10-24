from __future__ import annotations

from pathlib import Path

import scripts.evaluate_writing_parity as evaluation


def test_donor_and_destination_cover_the_same_synthetic_writing_requirements() -> None:
    result = evaluation.evaluate()

    assert result["synthetic"] is True
    assert result["donor_commit"] == "0d0f8cbb495292a3f3d61fba75428f3136059ea8"
    assert result["donor_fixture_sha256"] == result["destination_fixture_sha256"]
    assert result["passed"] is True
    assert all(
        item["status"] in {"COVERED", "DIVERGENCE_JUSTIFIED"}
        for item in result["results"]
    )
    assert any(
        item["requirement"] == "Classe secundária marcada como suspeita"
        and item["status"] == "DIVERGENCE_JUSTIFIED"
        and item.get("justification")
        for item in result["results"]
    )
    assert any(
        item["requirement"] == "Alegações e responsabilidade não inferidas apenas pelo polo"
        and item["status"] == "DIVERGENCE_JUSTIFIED"
        and not item["donor_present"]
        and item["destination_present"]
        and item.get("justification")
        for item in result["results"]
    )


def test_destination_omission_is_a_failed_comparison(tmp_path: Path, monkeypatch) -> None:
    original = evaluation.DESTINATION.read_text(encoding="utf-8")
    altered = original.replace("Pessoa Sintética Alfa", "Pessoa omitida")
    monkeypatch.setattr(evaluation, "DESTINATION", tmp_path / "omission.md")
    evaluation.DESTINATION.write_text(altered, encoding="utf-8")

    result = evaluation.evaluate()

    missing_party = next(
        item for item in result["results"]
        if item["requirement"] == "Partes e polos explicitamente cadastrados"
    )
    assert result["passed"] is False
    assert missing_party["status"] == "FAIL_DESTINATION_OMISSION"
