from __future__ import annotations

from uuid import UUID

from app.claim_evidence import (
    attachment_evidence_ref,
    build_material_claims,
    claim_evidence_is_complete,
    claim_evidence_is_publishable,
    evidence_catalog,
    movement_evidence_ref,
    process_evidence_ref,
    validate_claim_evidence,
)


VERSION_ID = UUID("00000000-0000-0000-0000-000000000101")
STEP_ID = UUID("00000000-0000-0000-0000-000000000202")
CHUNK_ID = UUID("00000000-0000-0000-0000-000000000303")


def _context() -> dict:
    return {
        "_process_evidence_ref": process_evidence_ref(VERSION_ID),
        "_selected_sources": [
            {
                "step_id": STEP_ID,
                "evidence_ref": movement_evidence_ref(STEP_ID),
            }
        ],
        "_attachment_sources": [
            {
                "attachment_chunk_id": CHUNK_ID,
                "evidence_ref": attachment_evidence_ref(CHUNK_ID),
            }
        ],
    }


def _payload() -> dict:
    return {
        "synthesis": "Síntese factual.",
        "timeline": ["Movimento relevante."],
        "current_status": "Situação atual.",
        "attention": ["Sem divergência material."],
        "decisions": ["Pedido deferido."],
        "deadlines": [],
        "related_processes": [],
        "attachments": ["Documento relevante."],
    }


def test_evidence_catalog_accepts_only_scoped_application_refs() -> None:
    catalog = evidence_catalog(_context())

    assert set(catalog) == {
        process_evidence_ref(VERSION_ID),
        movement_evidence_ref(STEP_ID),
        attachment_evidence_ref(CHUNK_ID),
    }
    assert catalog[process_evidence_ref(VERSION_ID)].kind == "process"
    assert catalog[movement_evidence_ref(STEP_ID)].step_id == STEP_ID
    assert catalog[attachment_evidence_ref(CHUNK_ID)].attachment_chunk_id == CHUNK_ID


def test_valid_claims_cover_process_movement_and_attachment_refs() -> None:
    payload = _payload()
    payload["claims"] = [
        {
            "claim_id": "synthesis",
            "text": payload["synthesis"],
            "evidence_refs": [process_evidence_ref(VERSION_ID)],
        },
        {
            "claim_id": "current_status",
            "text": payload["current_status"],
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
        {
            "claim_id": "timeline:0",
            "text": payload["timeline"][0],
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
        {
            "claim_id": "attention:0",
            "text": payload["attention"][0],
            "evidence_refs": [process_evidence_ref(VERSION_ID)],
        },
        {
            "claim_id": "decisions:0",
            "text": payload["decisions"][0],
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
        {
            "claim_id": "attachments:0",
            "text": payload["attachments"][0],
            "evidence_refs": [attachment_evidence_ref(CHUNK_ID)],
        },
    ]

    claims, errors = validate_claim_evidence(payload, _context())

    assert errors == []
    assert [claim.claim_id for claim in claims] == [
        "synthesis",
        "current_status",
        "timeline:0",
        "attention:0",
        "decisions:0",
        "attachments:0",
    ]


def test_missing_unknown_duplicate_and_cross_scope_refs_fail_closed() -> None:
    payload = _payload()
    other_step = UUID("00000000-0000-0000-0000-000000000999")
    payload["claims"] = [
        {
            "claim_id": "synthesis",
            "text": payload["synthesis"],
            "evidence_refs": [],
        },
        {
            "claim_id": "current_status",
            "text": payload["current_status"],
            "evidence_refs": [movement_evidence_ref(other_step)],
        },
        {
            "claim_id": "timeline:0",
            "text": payload["timeline"][0],
            "evidence_refs": [
                movement_evidence_ref(STEP_ID),
                movement_evidence_ref(STEP_ID),
            ],
        },
        {
            "claim_id": "decisions:0",
            "text": "Texto divergente.",
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
    ]

    _, errors = validate_claim_evidence(payload, _context())

    assert "material claim lacks evidence_refs: synthesis" in errors
    assert any(error.startswith("unknown evidence ref for current_status:") for error in errors)
    assert any(error.startswith("duplicate evidence ref for timeline:0:") for error in errors)
    assert "claim text does not match structured field: decisions:0" in errors
    assert "missing material claim provenance: attention:0" in errors
    assert "missing material claim provenance: attachments:0" in errors


def test_missing_claims_array_is_rejected() -> None:
    claims, errors = validate_claim_evidence(_payload(), _context())

    assert claims == []
    assert errors == ["structured summary must include claims"]


def test_deterministic_builder_covers_every_material_field() -> None:
    payload = _payload()
    refs = [movement_evidence_ref(STEP_ID)]

    built = build_material_claims(payload, evidence_refs=refs)

    assert [item["claim_id"] for item in built] == [
        "synthesis",
        "current_status",
        "timeline:0",
        "attention:0",
        "decisions:0",
        "attachments:0",
    ]
    assert all(item["evidence_refs"] == refs for item in built)


def test_attention_items_require_claim_provenance() -> None:
    payload = _payload()
    complete = build_material_claims(
        payload,
        evidence_refs=[movement_evidence_ref(STEP_ID)],
    )
    assert any(item["claim_id"] == "attention:0" for item in complete)

    without_attention = [
        item for item in complete if item["claim_id"] != "attention:0"
    ]
    structured = _structured_output(payload, complete)

    assert claim_evidence_is_complete(structured, without_attention) is False


def _structured_output(
    payload: dict,
    claims: list[dict] | None = None,
) -> dict:
    summary = dict(payload)
    if claims is not None:
        summary["claims"] = [dict(item) for item in claims]
    return {
        "schema_version": 2,
        "process": {
            "cnj": "0000000-00.2026.8.21.0001",
            "class_name": None,
            "court": None,
            "header": {},
            "parties": [],
        },
        "summary": summary,
    }


def test_publication_gate_rejects_tampered_process_projection() -> None:
    payload = _payload()
    complete = build_material_claims(
        payload,
        evidence_refs=[movement_evidence_ref(STEP_ID)],
    )
    structured = _structured_output(payload, complete)
    process = {
        "code": "0000000-00.2026.8.21.0001",
        "class_name": None,
        "court": None,
        "header": {},
        "parties": [],
    }

    assert claim_evidence_is_complete(structured, complete) is True
    assert claim_evidence_is_publishable(
        structured,
        complete,
        process=process,
    ) is True

    tampered = {
        **structured,
        "process": {
            **structured["process"],
            "parties": [{"name": "Parte injetada"}],
        },
    }
    assert claim_evidence_is_complete(tampered, complete) is True
    assert claim_evidence_is_publishable(
        tampered,
        complete,
        process=process,
    ) is False


def test_publication_gate_rejects_contradicted_semantic_verification() -> None:
    payload = _payload()
    complete = build_material_claims(
        payload,
        evidence_refs=[movement_evidence_ref(STEP_ID)],
    )
    structured = _structured_output(payload, complete)
    process = {
        "code": "0000000-00.2026.8.21.0001",
        "class_name": None,
        "court": None,
        "header": {},
        "parties": [],
    }
    persisted = [dict(item) for item in complete]
    persisted[0] = {
        **persisted[0],
        "verification_status": "contradicted",
        "sources": [
            {
                "evidence_ref": movement_evidence_ref(STEP_ID),
                "verification_status": "contradicted",
            }
        ],
    }

    assert claim_evidence_is_publishable(
        structured,
        persisted,
        process=process,
    ) is False


def test_publication_gate_allows_explicit_not_evaluated_semantic_status() -> None:
    payload = _payload()
    complete = build_material_claims(
        payload,
        evidence_refs=[movement_evidence_ref(STEP_ID)],
    )
    structured = _structured_output(payload, complete)
    process = {
        "code": "0000000-00.2026.8.21.0001",
        "class_name": None,
        "court": None,
        "header": {},
        "parties": [],
    }
    persisted = [
        {
            **item,
            "verification_status": "not_evaluated",
            "sources": [
                {
                    "evidence_ref": movement_evidence_ref(STEP_ID),
                    "verification_status": "not_evaluated",
                }
            ],
        }
        for item in complete
    ]

    assert claim_evidence_is_publishable(
        structured,
        persisted,
        process=process,
    ) is True


def test_publication_completeness_rejects_noncanonical_document() -> None:
    payload = _payload()
    refs = [movement_evidence_ref(STEP_ID)]
    complete = build_material_claims(payload, evidence_refs=refs)
    structured = _structured_output(payload, complete)

    tampered = {**structured, "debug": "must not publish"}
    assert claim_evidence_is_complete(tampered, complete) is False


def test_publication_completeness_requires_exact_claim_coverage() -> None:
    payload = _payload()
    refs = [movement_evidence_ref(STEP_ID)]
    complete = build_material_claims(payload, evidence_refs=refs)

    structured = _structured_output(payload, complete)
    assert claim_evidence_is_complete(structured, complete) is True

    missing = complete[:-1]
    assert claim_evidence_is_complete(structured, missing) is False

    empty_ref = [dict(item) for item in complete]
    empty_ref[0] = {**empty_ref[0], "evidence_refs": []}
    assert claim_evidence_is_complete(structured, empty_ref) is False

    wrong_text = [dict(item) for item in complete]
    wrong_text[0] = {**wrong_text[0], "text": "Texto diferente."}
    assert claim_evidence_is_complete(structured, wrong_text) is False


def test_publication_completeness_requires_exact_declared_refs() -> None:
    payload = _payload()
    declared = build_material_claims(
        payload,
        evidence_refs=[
            process_evidence_ref(VERSION_ID),
            movement_evidence_ref(STEP_ID),
        ],
    )
    structured = _structured_output(payload, declared)

    assert claim_evidence_is_complete(structured, declared) is True

    missing_ref = [dict(item) for item in declared]
    missing_ref[0] = {
        **missing_ref[0],
        "evidence_refs": [process_evidence_ref(VERSION_ID)],
    }
    assert claim_evidence_is_complete(structured, missing_ref) is False

    reversed_refs = [dict(item) for item in declared]
    reversed_refs[0] = {
        **reversed_refs[0],
        "evidence_refs": [
            movement_evidence_ref(STEP_ID),
            process_evidence_ref(VERSION_ID),
        ],
    }
    assert claim_evidence_is_complete(structured, reversed_refs) is False


def test_publication_completeness_rejects_mismatched_persisted_claim_class() -> None:
    payload = _payload()
    complete = build_material_claims(
        payload,
        evidence_refs=[movement_evidence_ref(STEP_ID)],
    )
    structured = _structured_output(payload, complete)
    persisted = [dict(item) for item in complete]
    persisted[0] = {**persisted[0], "claim_class": "decision"}

    assert claim_evidence_is_complete(structured, persisted) is False


def test_publication_completeness_rejects_legacy_or_duplicate_provenance() -> None:
    payload = _payload()
    refs = [movement_evidence_ref(STEP_ID)]
    complete = build_material_claims(payload, evidence_refs=refs)

    assert claim_evidence_is_complete(None, complete) is False
    assert claim_evidence_is_complete({"schema_version": 1}, complete) is False
    assert claim_evidence_is_complete(_structured_output(payload), complete) is False
    assert claim_evidence_is_complete(
        _structured_output(payload, complete),
        [*complete, dict(complete[0])],
    ) is False


def test_duplicate_material_claim_id_is_rejected() -> None:
    payload = _payload()
    ref = movement_evidence_ref(STEP_ID)
    payload["claims"] = build_material_claims(payload, evidence_refs=[ref])
    payload["claims"].append(dict(payload["claims"][0]))

    _, errors = validate_claim_evidence(payload, _context())

    assert "duplicate claim_id: synthesis" in errors


def test_malformed_and_empty_claim_fields_fail_closed() -> None:
    payload = _payload()
    valid_ref = movement_evidence_ref(STEP_ID)
    payload["claims"] = [
        {
            "claim_id": "",
            "text": payload["synthesis"],
            "evidence_refs": [valid_ref],
        },
        {
            "claim_id": "current_status",
            "text": "",
            "evidence_refs": [valid_ref],
        },
        {
            "claim_id": "timeline:0",
            "text": payload["timeline"][0],
            "evidence_refs": ["m-not-a-uuid"],
        },
        {
            "claim_id": "decisions:0",
            "text": payload["decisions"][0],
            "evidence_refs": [""],
        },
        {
            "claim_id": "attachments:0",
            "text": payload["attachments"][0],
            "evidence_refs": [valid_ref],
        },
    ]

    _, errors = validate_claim_evidence(payload, _context())

    assert "invalid claim_id: <empty>" in errors
    assert "claim text does not match structured field: current_status" in errors
    assert "invalid evidence ref for timeline:0: m-not-a-uuid" in errors
    assert "invalid evidence ref for decisions:0: <empty>" in errors
    assert "missing material claim provenance: synthesis" in errors


def test_publication_completeness_rejects_malformed_ref() -> None:
    payload = _payload()
    complete = build_material_claims(
        payload,
        evidence_refs=[movement_evidence_ref(STEP_ID)],
    )
    malformed = [dict(item) for item in complete]
    malformed[0] = {**malformed[0], "evidence_refs": ["m-not-a-uuid"]}

    assert claim_evidence_is_complete(
        _structured_output(payload, complete),
        malformed,
    ) is False


def test_publication_gate_requires_audit_metadata_for_evaluated_relations() -> None:
    payload = _payload()
    ref = movement_evidence_ref(STEP_ID)
    complete = build_material_claims(payload, evidence_refs=[ref])
    structured = _structured_output(payload, complete)
    process = {
        "code": "0000000-00.2026.8.21.0001",
        "class_name": None,
        "court": None,
        "header": {},
        "parties": [],
    }
    valid_hash = "a" * 64
    evaluated = [dict(item) for item in complete]
    evaluated[0] = {
        **evaluated[0],
        "verification_status": "supported",
        "verification_reason": "deterministic_facts_present",
        "sources": [
            {
                "evidence_ref": ref,
                "verification_status": "supported",
                "verification_reason": "deterministic_facts_present",
                "evidence_excerpt_sha256": valid_hash,
            }
        ],
    }

    if not claim_evidence_is_publishable(
        structured,
        evaluated,
        process=process,
    ):
        raise AssertionError("well-formed evaluated evidence should remain publishable")

    missing_hash = [dict(item) for item in evaluated]
    missing_hash[0] = {
        **evaluated[0],
        "sources": [
            {
                **evaluated[0]["sources"][0],
                "evidence_excerpt_sha256": None,
            }
        ],
    }
    if claim_evidence_is_publishable(
        structured,
        missing_hash,
        process=process,
    ):
        raise AssertionError("evaluated evidence without excerpt hash must fail closed")

    malformed_hash = [dict(item) for item in evaluated]
    malformed_hash[0] = {
        **evaluated[0],
        "sources": [
            {
                **evaluated[0]["sources"][0],
                "evidence_excerpt_sha256": "not-a-sha256",
            }
        ],
    }
    if claim_evidence_is_publishable(
        structured,
        malformed_hash,
        process=process,
    ):
        raise AssertionError("malformed evaluated evidence hash must fail closed")

    aggregate_only = [dict(item) for item in evaluated]
    aggregate_only[0] = {
        **evaluated[0],
        "sources": [
            {
                "evidence_ref": ref,
                "verification_status": "not_evaluated",
                "verification_reason": "semantic_verifier_not_run",
                "evidence_excerpt_sha256": None,
            }
        ],
    }
    if claim_evidence_is_publishable(
        structured,
        aggregate_only,
        process=process,
    ):
        raise AssertionError(
            "evaluated aggregate status without an evaluated relation must fail closed"
        )


def test_publication_gate_rejects_missing_reasons_and_malformed_legacy_hash() -> None:
    payload = _payload()
    ref = movement_evidence_ref(STEP_ID)
    complete = build_material_claims(payload, evidence_refs=[ref])
    structured = _structured_output(payload, complete)
    process = {
        "code": "0000000-00.2026.8.21.0001",
        "class_name": None,
        "court": None,
        "header": {},
        "parties": [],
    }
    valid_hash = "b" * 64
    base = [dict(item) for item in complete]
    base[0] = {
        **base[0],
        "verification_status": "supported",
        "verification_reason": "deterministic_facts_present",
        "sources": [
            {
                "evidence_ref": ref,
                "verification_status": "supported",
                "verification_reason": "deterministic_facts_present",
                "evidence_excerpt_sha256": valid_hash,
            }
        ],
    }

    for bad_reason in (None, "", "   "):
        missing_claim_reason = [dict(item) for item in base]
        missing_claim_reason[0] = {
            **base[0],
            "verification_reason": bad_reason,
        }
        if claim_evidence_is_publishable(
            structured,
            missing_claim_reason,
            process=process,
        ):
            raise AssertionError(
                f"evaluated claim reason {bad_reason!r} must fail closed"
            )

        missing_source_reason = [dict(item) for item in base]
        missing_source_reason[0] = {
            **base[0],
            "sources": [
                {
                    **base[0]["sources"][0],
                    "verification_reason": bad_reason,
                }
            ],
        }
        if claim_evidence_is_publishable(
            structured,
            missing_source_reason,
            process=process,
        ):
            raise AssertionError(
                f"evaluated source reason {bad_reason!r} must fail closed"
            )

    malformed_legacy_hash = [dict(item) for item in complete]
    malformed_legacy_hash[0] = {
        **malformed_legacy_hash[0],
        "verification_status": "not_evaluated",
        "verification_reason": "semantic_verifier_not_run",
        "sources": [
            {
                "evidence_ref": ref,
                "verification_status": "not_evaluated",
                "verification_reason": "semantic_verifier_not_run",
                "evidence_excerpt_sha256": "xyz",
            }
        ],
    }
    if claim_evidence_is_publishable(
        structured,
        malformed_legacy_hash,
        process=process,
    ):
        raise AssertionError("malformed non-null legacy hash must fail closed")
