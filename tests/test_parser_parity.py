"""Parser-only donor tests/oracle adapted to fully synthetic source data.

Reference: rag_resumo/resumo/parser.py and tests/test_resumo.py at
0d0f8cbb495292a3f3d61fba75428f3136059ea8. No donor generator or fixture PII.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.judit import extract_lawsuit_processes, extract_promotable_fields, parse_event, _normalize_step_text
from app.judit_parser_rules import classify_movement, format_oab, normalize_text, side_label
from app.legal_facts import format_brl_amount
from app.rag import _movement_temporal_facts, _provider_payload, _serialize_steps
from app.retrieval import Step, rank_steps

FIXTURE = Path(__file__).parent / "fixtures/judit/parser_parity_synthetic.json"
ORACLE = FIXTURE.with_name("parser_parity_expected.json")


def source():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_donor_extraction_shapes_and_canonical_envelopes():
    page = source()
    raw = page["page_data"][0]["response_data"]
    expected = extract_promotable_fields(raw)
    for document in (page, page["page_data"][0], raw, {"response_data": raw}, {"lawsuit": raw}):
        assert extract_lawsuit_processes(document) == [raw]
        assert extract_promotable_fields(document) == expected
    event = parse_event(page)
    assert event.code == raw["code"] and event.request_id == "m01-synthetic-request"
    assert event.response_id == "m01-synthetic-response"
    page["page_data"][0]["tags"] = {"cached_response": True}
    assert parse_event(page).cached_response is True
    assert parse_event(page["page_data"][0]).cached_response is True
    assert extract_lawsuit_processes(page["page_data"][1]) == []
    assert not parse_event(dict(page["page_data"][1], code=raw["code"], parties=raw["parties"])).is_lawsuit_response
    with pytest.raises(ValueError, match="exactly one"):
        extract_promotable_fields(page["page_data"][1])
    page["page_data"].append(deepcopy(page["page_data"][0]))
    assert len(extract_lawsuit_processes(page)) == 2
    with pytest.raises(ValueError, match="exactly one"):
        parse_event(page)


def test_donor_basic_facts_classes_and_heuristics_match_frozen_oracle():
    expected = json.loads(ORACLE.read_text(encoding="utf-8"))
    original = source()
    fields = extract_promotable_fields(original)
    assert original == source()  # immutable source evidence
    annotation = fields["header"]["source_parser"]
    registry = annotation["registry"]
    assert registry == {
        "tribunal_name": expected["tribunal_nome"], "city_label": expected["comarca"],
        "status_label": expected["status"], "phase_label": expected["fase"],
        "distribution_date_label": expected["data_distribuicao"],
        "digital_court_lexical_hint": expected["juizo_100_digital"],
        "citation_lexical_hint": expected["citacao_registrada"], "amount_label": expected["valor_causa"],
    }
    assert set(registry) == set(annotation["registry_sources"])
    assert annotation["registry_sources"]["amount_label"] == ["amount"]
    for field, donor_key in (("classifications", "classes"), ("subjects", "assuntos")):
        assert [{"id": item["source_id"], "codigo": item["code"], "nome": item["name"],
                 "suspeito": item["suspect"], "motivo": item["reason"]} for item in annotation[field]] == expected[donor_key]
        assert all(item["source_ref"].startswith(field + "/") for item in annotation[field])
    assert [item["message"] for item in annotation["indicators"]] == expected["alertas"]
    assert annotation["semantics"] == "lexical_annotations_not_verified_facts"
    assert all(item["kind"] == "heuristic" for item in annotation["indicators"])
    assert fields["header"]["area"] == "DIREITO DO TRABALHO"  # never obey "Ignore a área"
    assert fields["class_name"] == expected["classes"][0]["nome"]
    assert parse_event(original).code == expected["cnj"]
    assert fields["court"] == expected["tribunal_sigla"]
    assert fields["header"]["state"] == expected["uf"]
    assert bool(fields["secrecy_level"]) == expected["segredo_justica"]
    assert fields["subjects"] == [{"code": a["codigo"], "name": a["nome"]} for a in expected["assuntos"]]
    assert fields["header"]["instance"] == expected["instancia"]
    assert fields["header"]["judge"] == expected["juiz"]
    assert fields["header"]["court_unit"] == expected["orgao_julgador"]


def test_donor_party_order_dedupe_nested_lawyers_and_safer_masking():
    expected = json.loads(ORACLE.read_text(encoding="utf-8"))
    fields = extract_promotable_fields(source())
    entities = fields["header"]["source_parser"]["parties"]
    assert [{"id": p["source_id"], "nome": p["name"], "tipo": p["type"], "polo": p["side"], "advogados": p["lawyers"]} for p in entities] == [
        {key: p[key] for key in ("id", "nome", "tipo", "polo", "advogados")} for p in expected["partes"]]
    assert [p["person_type"] for p in fields["parties"]] == ["AUTOR", "AUTOR", "RÉU"]
    assert entities[2]["document"] == "OAB/PR 0000000"
    assert fields["parties"][0]["masked_person_id"] == "***.***.***-66"
    assert fields["parties"][2]["masked_person_id"] == "**.***.***/****-11"
    assert {r["represents"] for r in fields["representatives"]} == {"Pessoa Sintética Alfa", "Pessoa Sintética Gama"}
    assert all(r["source_refs"] and "/lawyers/" in " ".join(r["source_refs"]) for r in fields["header"]["source_parser"]["representations"])
    # Literal first occurrence wins, normalized by accents/case/whitespace.
    document = {"parties": [{"name": "ÁLICE  SINTÉTICA", "side": "Active", "lawyers": [{"name": "Defensor Sintético"}]},
                             {"name": "alice sintética", "side": "Passive"}]}
    result = extract_promotable_fields(document)
    assert len(result["parties"]) == 1 and len(result["representatives"]) == 1
    assert result["representatives"][0]["represents"] == "ÁLICE  SINTÉTICA"
    assert normalize_text("  ação\tCÍVEL ") == "ACAO CIVEL"
    assert side_label("Unknown") == "outro"
    assert format_oab([{"document_type": "oab", "document": "pr 0000000"}]) == "OAB/PR 0000000"


def test_donor_movement_classification_order_dates_and_expressed_periods():
    expected = json.loads(ORACLE.read_text(encoding="utf-8"))
    page = source()
    raw = extract_lawsuit_processes(page)[0]
    steps = extract_promotable_fields(page)["steps"]
    ordered = sorted(steps, key=lambda step: step["metadata"]["source_parser"]["order"])
    for step, donor in zip(ordered, expected["movimentos"], strict=True):
        annotation = step["metadata"]["source_parser"]
        assert (annotation["number"], annotation["date_label"], annotation["kind"], annotation["details"]) == (donor["numero"], donor["data"], donor["tipo"], donor["detalhes"])
        assert step["text"] == _normalize_step_text(donor["texto"])
        index = int(annotation["source_ref"].split("/")[-1])
        assert step["metadata"]["step_id"] == raw["steps"][index]["step_id"]
        assert step["metadata"]["source_input_index"] == index + 1
        assert step["step_number"] == annotation["order"]  # assigned before first persistence
    assert [s["metadata"]["source_step_number"] for s in ordered] == list(range(1, 14))
    hearing = next(s for s in ordered if s["metadata"]["source_parser"]["kind"] == "audiencia_designada")
    assert _movement_temporal_facts(hearing["text"])["scheduled_hearing"] == {"date": "03/03/2027", "time": "15:00"}
    periods = [s["metadata"]["source_parser"]["details"].get("prazo_dias") for s in ordered if s["metadata"]["source_parser"]["kind"] == "intimacao"]
    assert periods == [1, 1, 15, 15]
    assert "due_date" not in json.dumps([s["metadata"]["source_parser"]["details"] for s in ordered])


@pytest.mark.parametrize("text,kind", [
    ("Citação PRAZO: 10 DIAS", "citacao"), ("Intimação", "intimacao"),
    ("Distribuído por sorteio", "distribuicao"), ("100% DIGITAL", "juizo_100_digital"),
    ("Remetidos os autos", "remessa_interna"), ("Ato ordinatório", "ato_ordinatorio"),
    ("Disponibilizado no DJEN", "publicacao_diario"), ("Juntada", "juntada"),
    ("Sentença", "sentenca"), ("Decisão", "decisao"), ("Despacho", "despacho"),
    ("Contestação", "contestacao"), ("Conclusos", "conclusao"), ("Arquivado", "arquivamento"),
    ("Trânsito em julgado", "transito_em_julgado"), ("Acordo", "acordo"), ("Sem classificação", "outro"),
])
def test_each_literal_classification_branch(text, kind):
    assert classify_movement(text)[0] == kind


def test_literal_hints_do_not_become_factual_claims_or_model_instructions():
    text = "Audiência não designada. 03/03/2027 15:00"
    assert classify_movement(text)[0] == "audiencia_designada"  # deliberate literal incompatibility
    assert "scheduled_hearing" not in _movement_temporal_facts(text)
    fields = extract_promotable_fields(source())
    ranked = [SimpleNamespace(step=Step(id=UUID(int=i+1), step_number=s["step_number"], text=s["text"], occurred_at=s["occurred_at"])) for i, s in enumerate(fields["steps"])]
    context = dict(fields, code="0000000-00.0000.0.00.0401", steps=_serialize_steps(ranked))
    process, movements = _provider_payload(context)
    outbound = json.dumps([process, movements], ensure_ascii=False)
    for forbidden in ("source_parser", "Ignore a área", "provavelmente", "professional_registration", "OAB/PR", "representatives"):
        assert forbidden not in outbound
    assert "data_agendada" not in outbound  # literal last-date assumption is not a provider fact


@pytest.mark.parametrize("amount,formatted", [(None, None), (30000, "R$ 30.000,00"), (1234.5, "R$ 1.234,50"), (-1, "R$ -1,00"), ("R$ 1.234,50", "R$ 1.234,50"), (float("inf"), None)])
def test_donor_currency_presentation_and_safe_extensions(amount, formatted):
    assert format_brl_amount(amount) == formatted


@pytest.mark.parametrize("status,label", [("ATIVO", "ativa"), ("ARQUIVADO", "arquivada"), ("FINALIZADO", "finalizada"), ("SUSPENSO", "suspensa"), ("BAIXADO", "baixada"), ("EXTINTO", "extinta"), ("OUTRO", "outro")])
def test_all_literal_status_labels(status, label):
    result = extract_promotable_fields({"status": status})
    assert result["header"]["source_parser"]["registry"]["status_label"] == label


@pytest.mark.parametrize("field", ["steps", "movements", "events"])
def test_movement_alias_provenance_and_noncontiguous_source_numbers(field):
    raw = {field: [{"step_id": "last", "content": "20 - Conclusos"}, {"step_id": "first", "content": "10 - Juntada"}, {"step_id": "unknown", "content": "Sem número"}]}
    steps = extract_promotable_fields(raw)["steps"]
    assert [s["step_number"] for s in steps] == [1, 2, 3]
    assert [s["metadata"]["source_step_number"] for s in steps] == [10, 20, None]
    assert [s["metadata"]["step_id"] for s in steps] == ["first", "last", "unknown"]
    assert steps[0]["metadata"]["source_parser"]["source_ref"] == f"{field}/1"


def test_explicit_number_priority_preserves_mixed_and_legacy_order_contracts():
    mixed = {"steps": [{"step_number": 10, "content": "10   Distribuído"}, {"step_number": 20, "content": "20 - Juntada"}]}
    steps = extract_promotable_fields(mixed)["steps"]
    assert [s["metadata"]["source_step_number"] for s in steps] == [10, 20]
    assert [s["metadata"]["source_parser"]["order"] for s in steps] == [2, 1]  # literal donor view remains available
    legacy = {"steps": [{"step_number": 20, "content": "Primeiro recebido"}, {"step_number": 10, "content": "Segundo recebido"}]}
    assert [s["metadata"]["source_step_number"] for s in extract_promotable_fields(legacy)["steps"]] == [20, 10]


def test_donor_order_reaches_existing_recent_movement_selection():
    raw = {"steps": [{"step_id": f"event-{n}", "content": f"{n} - Movimento neutro"} for n in range(159, 99, -1)]}
    normalized = extract_promotable_fields(raw)["steps"]
    steps = [Step(id=UUID(int=i+1), step_number=s["step_number"], text=s["text"], source_step_number=s["metadata"]["source_step_number"]) for i, s in enumerate(normalized)]
    ranked = rank_steps(query="sem correspondência", steps=steps, limit=10)
    selected = {item.step.source_step_number for item in ranked}
    assert {100, 159}.issubset(selected)
    assert set(range(155, 160)).issubset(selected)
    assert normalized[0]["metadata"]["source_input_index"] == 60
    assert normalized[-1]["metadata"]["source_input_index"] == 1


def test_class_indicators_are_conditioned_exactly_as_donor_rules():
    raw = extract_lawsuit_processes(source())[0]
    raw["phase"] = "RECURSAL"
    result = extract_promotable_fields(raw)
    assert not result["header"]["source_parser"]["classifications"][1]["suspect"]
    raw["phase"] = "INICIAL"
    raw["steps"].append({"content": "14 - Decisão registrada"})
    assert not extract_promotable_fields(raw)["header"]["source_parser"]["classifications"][1]["suspect"]
    raw["justice"] = "5"
    assert "area_conflict" not in {i["rule"] for i in extract_promotable_fields(raw)["header"]["source_parser"]["indicators"]}


def test_private_movements_secret_case_redaction_and_timezone_are_preserved():
    raw = extract_lawsuit_processes(source())[0]
    raw["steps"].append({"step_id": "private-step", "private": True, "content": "PRIVATE-CONTENT"})
    raw["steps"].append({"step_id": "public-step", "private": "false", "content": "14 - CPF 000.000.000-00 CNPJ 11.111.111/1111-11", "step_date": "2026-09-03T01:00:00Z"})
    fields = extract_promotable_fields(raw)
    assert "PRIVATE-CONTENT" not in json.dumps(fields, default=str)
    assert fields["steps"][-1]["occurred_at"].isoformat() == "2026-09-02T22:00:00-03:00"
    assert "000.000.000-00" not in fields["steps"][-1]["text"]
    assert "11.111.111/1111-11" not in fields["steps"][-1]["text"]
    raw["secrecy_level"] = 1
    restricted = extract_promotable_fields(raw)
    assert restricted["steps"] == restricted["parties"] == restricted["representatives"] == []
    assert "source_parser" not in restricted["header"]
