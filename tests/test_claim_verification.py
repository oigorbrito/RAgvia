from __future__ import annotations

from uuid import UUID

from app.claim_evidence import MaterialClaim, process_evidence_ref
from app.claim_verification import verification_errors, verify_material_claims


VERSION_ID = UUID("00000000-0000-0000-0000-000000000101")
STEP_ID = UUID("00000000-0000-0000-0000-000000000102")
CHUNK_ID = UUID("00000000-0000-0000-0000-000000000103")


def _context() -> dict:
    return {
        "code": "0000000-00.2026.8.21.0001",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "header": {"amount": "1000.00"},
        "parties": [{"name": "Maria da Silva", "side": "active"}],
        "step_count": 1,
        "_process_evidence_ref": process_evidence_ref(VERSION_ID),
        "_selected_sources": [
            {
                "evidence_ref": f"m-{STEP_ID.hex}",
                "step_id": STEP_ID,
            }
        ],
        "_attachment_sources": [
            {
                "evidence_ref": f"a-{CHUNK_ID.hex}",
                "attachment_chunk_id": CHUNK_ID,
            }
        ],
        "steps": [
            {
                "evidence_ref": f"m-{STEP_ID.hex}",
                "step_number": 1,
                "occurred_at": "2026-09-10T12:00:00-03:00",
                "title": "Audiência",
                "text": "Audiência realizada em 10/09/2026.",
            }
        ],
        "attachments": [
            {
                "evidence_ref": f"a-{CHUNK_ID.hex}",
                "source_attachment_id": "doc-1",
                "page_start": 2,
                "page_end": 2,
                "char_start": 120,
                "char_end": 180,
                "text": "Decisão anexada determina pagamento de R$ 1.000,00.",
            }
        ],
    }


def test_process_amount_mismatch_is_contradicted() -> None:
    claim = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="Valor da causa: R$ 2.000,00.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    verification = verify_material_claims([claim], _context())

    result = verification["synthesis"]
    assert result.status == "contradicted"
    assert result.relations[0].status == "contradicted"
    assert verification_errors(verification) == [
        "claim contradicted by cited evidence: synthesis"
    ]


def test_different_cnj_on_process_ref_is_insufficient_not_contradicted() -> None:
    claim = MaterialClaim(
        claim_id="related_processes:0",
        claim_class="related_process",
        text="Processo relacionado 9999999-99.2026.8.21.9999.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    result = verify_material_claims([claim], _context())["related_processes:0"]

    assert result.status == "insufficient"
    assert result.reason == "cited_sources_do_not_support_all_deterministic_facts"


def test_process_step_count_is_supported_and_mismatch_is_contradicted() -> None:
    supported = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="O processo possui 1 movimentos.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )
    contradicted = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="O processo possui 2 movimentos.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    assert verify_material_claims([supported], _context())["synthesis"].status == "supported"
    assert (
        verify_material_claims([contradicted], _context())["synthesis"].status
        == "contradicted"
    )


def test_partial_phrase_is_not_treated_as_exact_lexical_support() -> None:
    context = _context()
    context["steps"][0]["text"] = "Pedido deferido parcialmente"
    claim = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Pedido deferido.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    result = verify_material_claims([claim], context)["timeline:0"]

    assert result.status == "not_evaluated"
    assert result.reason == "no_deterministic_fact_anchor"


def test_terminal_punctuation_does_not_block_exact_movement_support() -> None:
    context = _context()
    context["steps"][0]["text"] = "SENTENÇA proferida"
    claim = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="SENTENÇA proferida.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    result = verify_material_claims([claim], context)["timeline:0"]

    assert result.status == "supported"
    assert result.reason == "exact_text_present_in_cited_source"


def test_labeled_amount_without_currency_prefix_is_verified() -> None:
    claim = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="Valor da causa: 2.000,00.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["synthesis"].status == "contradicted"
    assert verification_errors(verification) == [
        "claim contradicted by cited evidence: synthesis"
    ]


def test_unlabeled_amount_mismatch_is_insufficient_not_contradicted() -> None:
    claim = MaterialClaim(
        claim_id="current_status",
        claim_class="current_status",
        text="Foi determinado pagamento de R$ 2.000,00.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    result = verify_material_claims([claim], _context())["current_status"]

    assert result.status == "insufficient"
    assert result.reason == "cited_sources_do_not_support_all_deterministic_facts"


def test_movement_date_anchor_is_supported() -> None:
    claim = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Audiência realizada em 10/09/2026.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    result = verify_material_claims([claim], _context())["timeline:0"]

    assert result.status == "supported"
    assert result.deterministic_fact_count >= 1
    assert result.relations[0].status == "supported"


def test_iso_timestamp_uses_sao_paulo_date_without_accepting_raw_utc_day() -> None:
    context = _context()
    context["steps"][0]["occurred_at"] = "2026-09-10T01:30:00+00:00"
    context["steps"][0]["text"] = "Audiência registrada."

    wrong_day = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Audiência registrada em 10/09/2026.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )
    local_day = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Audiência registrada em 09/09/2026.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    assert verify_material_claims([wrong_day], context)["timeline:0"].status == "insufficient"
    assert verify_material_claims([local_day], context)["timeline:0"].status == "supported"


def test_known_party_anchor_handles_punctuation_in_claim_and_process_json() -> None:
    claim = MaterialClaim(
        claim_id="current_status",
        claim_class="current_status",
        text="Maria da Silva, parte autora, consta no processo.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    result = verify_material_claims([claim], _context())["current_status"]

    assert result.status == "supported"
    assert result.deterministic_fact_count == 1


def test_missing_deterministic_date_is_insufficient_and_retryable() -> None:
    claim = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Audiência realizada em 11/09/2026.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["timeline:0"].status == "insufficient"
    assert verification_errors(verification) == [
        "claim has insufficient cited evidence: timeline:0"
    ]


def test_unanchored_intrinsically_material_claim_is_retryable() -> None:
    claim = MaterialClaim(
        claim_id="decisions:0",
        claim_class="decision",
        text="Houve decisão relevante sobre a controvérsia.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["decisions:0"].status == "not_evaluated"
    assert verification["decisions:0"].claim_class == "decision"
    assert verification["decisions:0"].deterministic_fact_count == 0
    assert verification_errors(verification) == [
        "material claim was not evaluated: decisions:0"
    ]


def test_unanchored_narrative_current_status_remains_measured_without_retry() -> None:
    claim = MaterialClaim(
        claim_id="current_status",
        claim_class="current_status",
        text="O processo segue em andamento.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["current_status"].status == "not_evaluated"
    assert verification_errors(verification) == []


def test_generic_attention_can_remain_measured_not_evaluated_without_retry() -> None:
    claim = MaterialClaim(
        claim_id="attention:0",
        claim_class="attention",
        text="Nenhuma divergência objetiva identificada.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["attention:0"].status == "not_evaluated"
    assert verification["attention:0"].claim_class == "attention"
    assert verification_errors(verification) == []


def test_attachment_verification_preserves_available_positions_and_excerpt_hash() -> None:
    claim = MaterialClaim(
        claim_id="attachments:0",
        claim_class="attachment",
        text="Decisão anexada determina pagamento de R$ 1.000,00.",
        evidence_refs=(f"a-{CHUNK_ID.hex}",),
    )

    result = verify_material_claims([claim], _context())["attachments:0"]
    relation = result.relations[0]

    assert result.status == "supported"
    assert relation.status == "supported"
    assert relation.page_start == 2
    assert relation.page_end == 2
    assert relation.char_start == 120
    assert relation.char_end == 180
    assert relation.evidence_excerpt is not None
    assert relation.evidence_excerpt_sha256 is not None
    assert len(relation.evidence_excerpt_sha256) == 64


def test_any_cited_canonical_contradiction_wins_over_other_support() -> None:
    context = _context()
    context["attachments"][0]["text"] = "Valor da causa: R$ 2.000,00."
    claim = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="Valor da causa: R$ 2.000,00.",
        evidence_refs=(
            process_evidence_ref(VERSION_ID),
            f"a-{CHUNK_ID.hex}",
        ),
    )

    verification = verify_material_claims([claim], context)
    result = verification["synthesis"]

    assert result.status == "contradicted"
    assert {relation.status for relation in result.relations} == {
        "contradicted",
        "supported",
    }
    assert verification_errors(verification) == [
        "claim contradicted by cited evidence: synthesis"
    ]


def test_multiple_cited_sources_can_support_different_deterministic_facts() -> None:
    claim = MaterialClaim(
        claim_id="decisions:0",
        claim_class="decision",
        text="Em 10/09/2026 foi registrado valor de R$ 1.000,00.",
        evidence_refs=(
            f"m-{STEP_ID.hex}",
            process_evidence_ref(VERSION_ID),
        ),
    )

    result = verify_material_claims([claim], _context())["decisions:0"]

    # Each source alone is incomplete for the full sentence. The first deterministic
    # baseline therefore does not overclaim semantic entailment across sources.
    assert result.status == "supported"
    assert {relation.status for relation in result.relations} == {"insufficient"}
