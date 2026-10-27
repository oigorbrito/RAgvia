from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.claim_evidence import MaterialClaim, process_evidence_ref
from app.claim_verification import verify_material_claims
from app.tpu_glossary import (
    LegalKnowledgeEntry,
    load_legal_knowledge,
    load_tpu_catalog,
    normalize_legal_term,
    rank_legal_knowledge_candidates,
    resolve_legal_knowledge,
)

ROOT = Path(__file__).parents[1]
ORACLE = json.loads((ROOT / "tests/fixtures/legal_knowledge/donor_oracle.json").read_text(encoding="utf-8"))


def _resolve(*, entries=None, semantic_scores=None, class_code=None, class_name=None, subjects=(), movements=()):
    return resolve_legal_knowledge(
        class_code=class_code,
        class_name=class_name,
        subjects=subjects,
        movements=movements,
        catalog=load_tpu_catalog(),
        entries=entries or load_legal_knowledge(),
        semantic_scores=semantic_scores,
    )


def test_donor_fixed_oracle_ids_and_order_match_exact_code_term_alias_and_movement():
    actual = [
        next(item for item in _resolve(class_code=c["code"] if c["kind"] == "classe" else None, class_name=c["name"] if c["kind"] == "classe" else None, subjects=[{"name": c["name"]}] if c["kind"] == "assunto" else [], movements=[{"text": c["name"]}] if c["kind"] == "movimento" else []) if item["kind"] == {"classe": "class", "assunto": "subject", "movimento": "movement"}[c["kind"]])
        for c in ORACLE["item_cases"][:4]
    ]
    assert [row["id"] for row in actual] == [case["result"]["id"] for case in ORACLE["item_cases"][:4]]
    assert actual[0]["match_method"] == "code"
    assert actual[1]["match_method"] == "term"
    assert actual[2]["match_method"] == "term"
    assert actual[3]["match_method"] == "term"
    assert actual[0]["source_ref"].startswith("https://")
    assert actual[0]["definition_sha256"] == hashlib.sha256(actual[0]["definition"].encode()).hexdigest()


def test_typed_term_filter_deliberately_fixes_donor_cross_type_alias_leak():
    donor = next(c for c in ORACLE["item_cases"] if c["kind"] == "assunto" and c["name"] == "JEC")
    assert donor["result"]["id"] == "classe-436"
    assert not any(item["kind"] == "subject" for item in _resolve(subjects=[{"name": "JEC"}]))
    assert next(item for item in _resolve(class_name="JEC") if item["kind"] == "class")["id"] == "classe-436"


def test_assunto_899_matches_audited_narrow_primary_source():
    entry = next(item for item in load_legal_knowledge() if item.id == "assunto-899")
    assert entry.definition == "Art. 1º Toda pessoa é capaz de direitos e deveres na ordem civil."
    assert entry.source_ref.endswith(" — Art. 1º")
    hits = _resolve(subjects=[{"code": "899", "name": "DIREITO CIVIL"}])
    matched = next(item for item in hits if item["id"] == "assunto-899")
    assert matched["definition"] == entry.definition
    assert matched["source_ref"] == entry.source_ref
    assert matched["definition_sha256"] == hashlib.sha256(entry.definition.encode()).hexdigest()


def test_code_precedence_does_not_add_a_second_name_matched_definition():
    resolved = _resolve(subjects=[{"code": "14033", "name": "RESPONSABILIDADE CIVIL"}])
    assert [item["id"] for item in resolved if item["kind"] == "subject"] == ["assunto-14033"]


def test_official_tpu_code_has_authority_and_supplement_does_not_replace_it():
    catalog = load_tpu_catalog()
    official = catalog.resolve(kind="class", code="7")
    assert official is not None
    supplementary = _resolve(class_code="7", class_name="PROCEDIMENTO COMUM CÍVEL")
    assert not any(item["kind"] == "class" for item in supplementary)
    assert official.definition in {item.definition for item in catalog.entries.values()}


def test_unknown_terms_and_codes_fail_closed_without_fuzzy_name_guess():
    donor = next(c for c in ORACLE["item_cases"] if c["name"] == "zzqx inexistente")
    assert donor["result"] is None
    assert not any(item["kind"] == "class" for item in _resolve(class_code="999999", class_name="zzqx inexistente"))
    assert not any(item["kind"] == "subject" for item in _resolve(subjects=[{"code": "999999", "name": "zzqx inexistente"}]))


def test_generic_semantic_ranker_matches_fixed_oracle_scores_filters_limits_and_hashes():
    oracle_entries = ORACLE["controlled_entries"]
    entries = tuple(
        LegalKnowledgeEntry(
            row["id"], {"classe": "class", "assunto": "subject"}[row["tipo"]],
            row["codigo"], row["termo"],
            "Explicação sintética Alfa." if row["id"] == "synthetic-class" else "Explicação sintética Beta.",
            tuple(row["sinonimos"]), "https://synthetic.invalid/source", "synthetic",
            row["donor_content_sha256"],
        ) for row in oracle_entries
    )
    scores = {"synthetic-class": 0.8, "synthetic-subject": 0.6}
    for case in ORACLE["controlled_cases"]:
        actual = rank_legal_knowledge_candidates(
            entries=entries, scores=scores, k=case["limit"],
            kinds=[{"classe": "class", "assunto": "subject"}[kind] for kind in case["kinds"]] if case["kinds"] else None,
            min_score=case["min_score"],
        )
        if case["limit"] == 0:
            assert actual == []
            assert case["results"]  # donor returns an item for k=0; destination rejects invalid limits
            continue
        assert [entry.id for entry, _ in actual] == [row["id"] for row in case["results"]]
        assert [score for _, score in actual] == [row["score"] for row in case["results"]]
        for entry, _ in actual:
            expected_hash = next(row["donor_content_sha256"] for row in oracle_entries if row["id"] == entry.id)
            assert hashlib.sha256(entry.definition.encode("utf-8")).hexdigest() == expected_hash


def test_semantic_thresholds_are_inclusive_capped_and_provenanced():
    entries = load_legal_knowledge()
    target = next(entry for entry in entries if entry.id == "mov-citacao")
    exact = _resolve(movements=[{"text": "conteúdo desconhecido"}], semantic_scores={
        normalize_legal_term("conteúdo desconhecido"): {target.id: 0.25}
    })
    assert any(item["id"] == target.id and item["match_method"] == "semantic_bge" for item in exact)
    below = _resolve(movements=[{"text": "conteúdo desconhecido"}], semantic_scores={
        normalize_legal_term("conteúdo desconhecido"): {target.id: 0.2499}
    })
    assert not any(item["id"] == target.id for item in below)
    class_entry = next(entry for entry in entries if entry.id == "classe-reclamacao-trabalhista")
    accepted = _resolve(class_name="incidente não cadastrado", semantic_scores={
        normalize_legal_term("incidente não cadastrado"): {class_entry.id: 0.35}
    })
    rejected = _resolve(class_name="incidente não cadastrado", semantic_scores={
        normalize_legal_term("incidente não cadastrado"): {class_entry.id: 0.3499}
    })
    assert [item["id"] for item in accepted if item["kind"] == "class"] == [class_entry.id]
    assert not any(item["kind"] == "class" for item in rejected)
    assert accepted[0]["match_method"] == "semantic_bge"
    assert accepted[0]["score"] == 0.35


def test_semantic_movement_retrieval_uses_donor_k_two_and_deterministic_ties():
    entries = load_legal_knowledge()
    candidates = [entry for entry in entries if entry.kind == "movement"][:3]
    query = "movimento não reconhecido"
    scores = {entry.id: 0.31 for entry in candidates}
    resolved = _resolve(movements=[{"text": query}], semantic_scores={normalize_legal_term(query): scores})
    actual = [item["id"] for item in resolved if item["kind"] == "movement"]
    assert actual == sorted(entry.id for entry in candidates)[:2]


def test_context_deduplicates_repeated_movements_by_entry_id_and_keeps_priority():
    context = _resolve(
        class_code="436",
        class_name="PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL",
        subjects=[{"code": "14033", "name": "INDENIZAÇÃO POR DANO MORAL"}],
        movements=[{"text": "Intimação eletrônica expedida"}, {"text": "INTIMAÇÃO registrada"}],
    )
    ids = [item["id"] for item in context]
    assert ids[:2] == ["classe-436", "assunto-14033"]
    assert ids.count("mov-intimacao") == 1
    assert next(item for item in context if item["id"] == "mov-intimacao")["match_method"] == "term"


def test_every_transported_explanation_has_identified_primary_source_and_hash():
    entries = load_legal_knowledge()
    sources = json.loads((ROOT / "data/legal-knowledge/sources.json").read_text(encoding="utf-8"))
    known = {item["id"]: item for item in sources["documents"]}
    assert len(entries) == 26
    for entry in entries:
        assert entry.source_ref.startswith("https://")
        assert entry.source_document in known
        assert len(entry.definition_sha256) == 64
        assert entry.donor_mapping_verified_by_cnj is False
    assert ORACLE["item_cases"][-1]["result"] is None


def test_legal_explanation_can_be_verified_against_retrieved_exact_source():
    entry = next(item for item in _resolve(movements=[{"text": "CITAÇÃO"}]) if item["id"] == "mov-citacao")
    explanation = entry["definition"].split(". ", 1)[1].split(". ", 1)[1]
    claim = MaterialClaim("synthesis", "synthesis", explanation, (process_evidence_ref(uuid4()),))
    verification = verify_material_claims([claim], {
        "_process_evidence_ref": claim.evidence_refs[0],
        "code": "6000000-00.2026.8.16.0001",
        "class_name": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL",
        "parties": [], "subjects": [], "header": {}, "step_count": 0,
        "legal_knowledge": [entry],
    })
    assert verification["synthesis"].status == "supported"
    assert verification["synthesis"].reason == "exact_text_present_in_cited_source"
