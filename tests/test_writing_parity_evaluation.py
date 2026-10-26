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


def test_explicit_runtime_document_cannot_pass_with_stale_archived_reference() -> None:
    archived = evaluation.DESTINATION.read_text(encoding="utf-8")
    runtime_result = evaluation.evaluate(destination_text=archived)
    assert runtime_result["passed"] is True
    assert runtime_result["destination_origin"] == "runtime_api"
    assert runtime_result["destination_document_sha256"] == evaluation.evaluate()[
        "destination_document_sha256"
    ]
    altered = archived.replace("Pessoa Sintética Alfa", "Parte omitida")
    result = evaluation.evaluate(destination_text=altered)
    assert result["passed"] is False
    assert result["destination_origin"] == "runtime_api"
    assert result["destination_document_sha256"] != runtime_result["destination_document_sha256"]
    assert any(
        row["status"] == "FAIL_DESTINATION_OMISSION" for row in result["results"]
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

def _criterion(result: dict, requirement: str) -> str:
    return next(
        row["status"] for row in result["results"]
        if row["requirement"] == requirement
    )


def test_legal_source_citation_allows_only_optional_article_prefix() -> None:
    archived = evaluation.DESTINATION.read_text(encoding="utf-8")
    prefix_free = archived.replace(
        "Art. 3º O Juizado Especial Cível tem competência",
        "3º O Juizado Especial Cível tem competência",
    )
    assert prefix_free != archived
    criterion = "Explicações de classe/assunto sustentadas por conteúdo recuperado"
    assert _criterion(evaluation.evaluate(destination_text=prefix_free), criterion) == "COVERED"

    changed_substance = prefix_free.replace(
        "competência para conciliação", "competência para nomeação"
    )
    assert changed_substance != prefix_free
    assert _criterion(
        evaluation.evaluate(destination_text=changed_substance), criterion
    ) == "FAIL_DESTINATION_OMISSION"


def test_scheduled_hearing_source_wording_preserves_temporal_meaning() -> None:
    archived = evaluation.DESTINATION.read_text(encoding="utf-8")
    original_line = next(
        line for line in archived.splitlines()
        if line.startswith("- Em 01/09/2026") and "foi designada audiência" in line
    )
    raw_line = (
        "- Em 01/09/2026. AUDIÊNCIA DE CONCILIAÇÃO DESIGNADA - "
        "LOCAL CONCILIAÇÃO JUIZADO CÍVEL - 03/03/2027 15:00"
    )
    source_wording = archived.replace(original_line, raw_line)
    criterion = "Data do ato separada de data/horário agendado"
    assert _criterion(
        evaluation.evaluate(destination_text=source_wording), criterion
    ) == "COVERED"

    held = source_wording.replace(
        "AUDIÊNCIA DE CONCILIAÇÃO DESIGNADA",
        "AUDIÊNCIA DE CONCILIAÇÃO REALIZADA",
    )
    assert _criterion(
        evaluation.evaluate(destination_text=held), criterion
    ) == "FAIL_DESTINATION_OMISSION"

    disconnected = source_wording.replace(
        " - 03/03/2027 15:00", ""
    ) + "\n- Data avulsa: 03/03/2027 15:00\n"
    assert _criterion(
        evaluation.evaluate(destination_text=disconnected), criterion
    ) == "FAIL_DESTINATION_OMISSION"

