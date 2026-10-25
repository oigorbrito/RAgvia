from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/judit/parser_parity_synthetic.json"
EXPECTED = ROOT / "tests/fixtures/judit/parser_parity_expected.json"
DONOR = ROOT / "docs/evaluation/evidence/m03-donor-template.json"
DESTINATION = ROOT / "docs/evaluation/evidence/m03-synthetic-summary.md"
OUTPUT = ROOT / "docs/evaluation/evidence/m03-writing-coverage.json"
DONOR_SHA = "0d0f8cbb495292a3f3d61fba75428f3136059ea8"


def _text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).casefold()


def _result(
    requirement: str,
    donor_present: bool,
    destination_present: bool,
    *,
    divergence: str | None = None,
) -> dict[str, Any]:
    if not donor_present:
        status = "FAIL_DONOR_REFERENCE_MISSING"
    elif destination_present:
        status = "COVERED"
    elif divergence:
        status = "DIVERGENCE_JUSTIFIED"
    else:
        status = "FAIL_DESTINATION_OMISSION"
    return {
        "requirement": requirement,
        "donor_present": donor_present,
        "destination_present": destination_present,
        "status": status,
        **({"justification": divergence} if divergence else {}),
    }


def evaluate(*, destination_text: str | None = None) -> dict[str, Any]:
    """Compare donor oracle with the supplied canonical published document.

    No value means the archived M03 reference; integration callers must pass
    the actual iaSummary returned by the current runtime/API.
    """
    fixture_bytes = FIXTURE.read_bytes()
    fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()
    donor_record = json.loads(DONOR.read_text(encoding="utf-8"))
    if donor_record.get("provenance", {}).get("commit") != DONOR_SHA:
        raise ValueError("donor oracle commit does not match the fixed reference")
    if donor_record.get("provenance", {}).get("fixture_sha256") != fixture_sha:
        raise ValueError("donor oracle was not generated from the current parity fixture")
    donor = donor_record["output"]
    destination = DESTINATION.read_text(encoding="utf-8") if destination_text is None else destination_text
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    donor_intro = _text(donor["introducao"])
    donor_party = _text([item["descricao"] for item in donor["partes"]])
    donor_classes = _text([item["explicacao"] for item in donor["classes"]])
    donor_subjects = _text([item["explicacao"] for item in donor["assuntos"]])
    donor_events = _text(donor["movimentacoes"])
    donor_document = _text(donor_record["document"])
    target = _text(destination)
    timeline = destination.split("## Movimentações", 1)[-1].split("Estado atual:", 1)[0]
    target_timeline = _text(timeline)
    attention = _text(destination.split("## Pontos de atenção", 1)[-1])

    code = expected["cnj"]
    party_names = [p["nome"] for p in expected["partes"] if p["tipo"] != "ADVOGADO"]
    representatives = {
        lawyer
        for party in expected["partes"]
        for lawyer in party["advogados"]
    }
    subjects = [item["nome"] for item in expected["assuntos"]]
    main_class = expected["classes"][0]["nome"]
    suspect_classes = [item for item in expected["classes"] if item["suspeito"]]

    def in_both(needle: str, target_text: str = target, donor_text: str = donor_intro + donor_party + donor_events) -> tuple[bool, bool]:
        value = _text(needle)
        return value in donor_text, value in target_text

    results: list[dict[str, Any]] = []
    donor_has_id, target_has_id = in_both(code)
    results.append(_result("Identificação CNJ", donor_has_id, target_has_id))

    donor_parties = all(_text(name) in donor_document for name in party_names) and all(
        f"polo {_text(party['polo'])}" in donor_party
        for party in expected["partes"] if party["tipo"] != "ADVOGADO"
    )
    target_parties = all(_text(name) in target for name in party_names) and all(
        f"{_text(party['nome'])} — polo cadastrado: {_text(party['polo'])}" in target
        for party in expected["partes"] if party["tipo"] != "ADVOGADO"
    )
    results.append(_result("Partes e polos explicitamente cadastrados", donor_parties, target_parties))

    donor_representatives = all(_text(name) in donor_document and _text(name) in donor_party for name in representatives)
    represented_pairs = set()
    for line in destination.splitlines():
        match = re.fullmatch(
            r"-\s*(.+?)\s+\(representa:\s*(.+?)\)", line.strip()
        )
        if match:
            for represented in re.split(r"\s+e\s+", match.group(2)):
                represented_pairs.add(
                    (_text(match.group(1)).strip(), _text(represented).strip())
                )
    target_representatives = all(
        (_text(name).strip(), _text(party["nome"]).strip()) in represented_pairs
        for party in expected["partes"]
        for name in party["advogados"]
    )
    results.append(_result("Relações parte–representante registradas", donor_representatives, target_representatives))

    donor_pole_inferences = (
        "afirma ter sofrido algum prejuízo" in donor_party
        and "responsável pelos fatos" in donor_party
    )
    target_avoids_pole_inference = (
        "afirma ter sofrido algum prejuízo" not in target
        and "responsável pelos fatos" not in target
    )
    inference_difference = (
        "O template doador atribui prejuízo ao polo ativo e responsabilidade ao polo passivo; "
        "o destino descreve somente o polo registrado, sem converter cadastro em alegação ou mérito."
    )
    if not donor_pole_inferences:
        inference_result = _result(
            "Alegações e responsabilidade não inferidas apenas pelo polo", False, target_avoids_pole_inference
        )
    elif not target_avoids_pole_inference:
        inference_result = _result(
            "Alegações e responsabilidade não inferidas apenas pelo polo", True, False
        )
    else:
        inference_result = {
            "requirement": "Alegações e responsabilidade não inferidas apenas pelo polo",
            "donor_present": False,
            "destination_present": True,
            "status": "DIVERGENCE_JUSTIFIED",
            "justification": inference_difference,
        }
    results.append(inference_result)

    donor_main_class = _text(main_class) in donor_document
    target_main_class = _text(main_class) in target
    results.append(_result("Classe principal", donor_main_class, target_main_class))
    suspicious_present = bool(suspect_classes) and all(_text(item["nome"]) in donor_document for item in suspect_classes)
    suspicious_target = all(_text(item["nome"]) in target for item in suspect_classes)
    results.append(_result(
        "Classe secundária marcada como suspeita",
        suspicious_present,
        suspicious_target,
        divergence=(
            "O doador explica a classificação EMBARGOS embora M01 a marque como suspeita; "
            "o contrato canônico publica a classe principal e não apresenta heurística como fato processual."
        ) if suspicious_present and not suspicious_target else None,
    ))

    donor_subjects_covered = all(_text(name) in donor_document for name in subjects)
    target_subjects_covered = all(_text(name) in target for name in subjects)
    results.append(_result("Cobertura dos assuntos cadastrados", donor_subjects_covered, target_subjects_covered))

    class_quote = "Art. 3º O Juizado Especial Cível tem competência para conciliação, processo e julgamento das causas cíveis de menor complexidade, inclusive as de valor até quarenta vezes o salário mínimo."
    subject_quote = "VI - a efetiva prevenção e reparação de danos patrimoniais e morais, individuais, coletivos e difusos"
    donor_explanations = (
        len(donor["classes"]) == len(expected["classes"])
        and len(donor["assuntos"]) == len(expected["assuntos"])
        and all(bool(_text(item["explicacao"]).strip()) for item in donor["classes"] + donor["assuntos"])
    )
    target_explanations = _text(class_quote) in target and _text(subject_quote) in target
    results.append(_result("Explicações de classe/assunto sustentadas por conteúdo recuperado", donor_explanations, target_explanations))

    act_date = expected["movimentos"][0]["data"]
    hearing = next(item for item in expected["movimentos"] if item["tipo"] == "audiencia_designada")
    scheduled = hearing["detalhes"]["data_agendada"]
    donor_temporal = act_date in donor["movimentacoes"] and scheduled in donor["movimentacoes"]
    target_temporal = act_date in timeline and scheduled in timeline and "não confirmação de realização" in timeline
    results.append(_result("Data do ato separada de data/horário agendado", donor_temporal, target_temporal))

    relevant = {"distribuicao", "juizo_100_digital", "audiencia_designada", "intimacao", "remessa_interna", "ato_ordinatorio", "publicacao_diario", "juntada"}
    donor_milestones = all(phrase in donor_events for phrase in (
        "distribuído por sorteio", "juízo 100% digital", "audiência de conciliação",
        "intimações eletrônicas", "remessas internas", "atos ordinatórios", "juntada",
    ))
    target_milestones = (
        "distribuído por sorteio" in target_timeline
        and "juízo 100% digital" in target_timeline
        and "audiência de conciliação" in target_timeline
        and "juntada de certidão" in target_timeline
        and target_timeline.count("remessa interna") == sum(m["tipo"] == "remessa_interna" for m in expected["movimentos"])
        and sum(int(match) for match in re.findall(r"(\d+) intimações", target_timeline)) == sum(m["tipo"] == "intimacao" for m in expected["movimentos"])
        and sum(int(match) for match in re.findall(r"(\d+) registros? de expediente administrativo", target_timeline)) == sum(m["tipo"] in {"ato_ordinatorio", "publicacao_diario"} for m in expected["movimentos"])
    )
    results.append(_result("Marcos e agrupamento administrativo sem perda de eventos", donor_milestones, target_milestones))

    date_sequence = []
    for line in timeline.splitlines():
        match = re.search(r"\d{2}/\d{2}/\d{4}", line)
        if match:
            raw_date = match.group(0)
            date_sequence.append(date.fromisoformat(f"{raw_date[6:]}-{raw_date[3:5]}-{raw_date[:2]}"))
    donor_order = [item["data"] for item in expected["movimentos"]]
    donor_ordered = donor_order == sorted(donor_order, key=lambda value: date.fromisoformat(value[6:] + "-" + value[3:5] + "-" + value[:2]))
    target_ordered = date_sequence == sorted(date_sequence)
    results.append(_result("Ordem cronológica dos movimentos", donor_ordered, target_ordered))

    absent_facts = (
        not any(m["tipo"] == "citacao" for m in expected["movimentos"])
        and not any(m["tipo"] in {"decisao", "sentenca"} for m in expected["movimentos"])
    )
    donor_limits = "ainda não há registro de citação" in donor_intro
    target_limits = absent_facts and all(term in attention for term in ("não registra citação", "decisão", "sentença", "não se infere"))
    results.append(_result("Informação ausente sem afirmar ocorrência", donor_limits, target_limits))

    donor_no_personal_docs = not any(
        p.get("documento") and re.search(r"\d{8,}", p["documento"])
        for p in donor["partes"]
    )
    target_no_personal_docs = not bool(re.search(r"\d{8,}", destination))
    results.append(_result(
        "Ausência de documentos pessoais no documento publicado",
        donor_no_personal_docs,
        target_no_personal_docs,
    ))

    # A provider stub proves deterministic identity/source retention, not
    # model-quality writing. Keep every other criterion and its gap visible.
    critical_names = {
        "Identificação CNJ",
        "Partes e polos explicitamente cadastrados",
        "Relações parte–representante registradas",
        "Classe principal",
        "Cobertura dos assuntos cadastrados",
        "Data do ato separada de data/horário agendado",
        "Ordem cronológica dos movimentos",
        "Ausência de documentos pessoais no documento publicado",
    }
    critical_rows = [
        row for row in results if row["requirement"] in critical_names
    ]
    if len(critical_rows) != len(critical_names):
        raise ValueError("incomplete deterministic preservation rubric")
    critical_passed = all(row["status"] == "COVERED" for row in critical_rows)
    unresolved = [
        row["requirement"]
        for row in results
        if row["status"] == "FAIL_DESTINATION_OMISSION"
    ]
    return {
        "synthetic": True,
        "deterministic_preservation_passed": critical_passed,
        "deterministic_preservation_count": len(critical_rows),
        "writing_quality_qualified": not unresolved,
        "writing_quality_gaps": unresolved,
        "rubric_count": len(results),
        "rubric_covered": sum(row["status"] == "COVERED" for row in results),
        "donor_commit": DONOR_SHA,
        "donor_fixture_sha256": donor_record["provenance"]["fixture_sha256"],
        "destination_fixture_sha256": fixture_sha,
        "destination_document_sha256": hashlib.sha256(destination.encode("utf-8")).hexdigest(),
        "destination_origin": "runtime_api" if destination_text is not None else "archived_m03",
        "comparison": "deterministic information coverage; wording equality not required",
        "results": results,
        "passed": all(item["status"] in {"COVERED", "DIVERGENCE_JUSTIFIED"} for item in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--destination", type=Path,
        help="current generated/published iaSummary document (required for live CI parity)",
    )
    args = parser.parse_args()
    result = evaluate(
        destination_text=args.destination.read_text(encoding="utf-8")
        if args.destination is not None else None
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "results": result["results"]}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
