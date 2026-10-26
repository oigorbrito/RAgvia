from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.blind_paired_legal_review import DIMENSIONS, aggregate, prepare
from scripts.evaluate_paired_legal_corpus import DONOR
from scripts.evaluate_writing_parity import DESTINATION, EXPECTED, FIXTURE


CASE = "m01-doc01-scheduled-hearing"


def _scoring(form: dict) -> dict:
    return {
        **form,
        "reviews": [
            {
                "reviewer_id": reviewer,
                "scores": {
                    "A": {key: 2 for key in DIMENSIONS},
                    "B": {key: 3 for key in DIMENSIONS},
                },
                "annotations": {
                    side: {
                        key: {
                            "justification": "Comparison to frozen source facts",
                            "document_location": "Movimentações section",
                            "source_refs": ["movimento:5"],
                        }
                        for key in DIMENSIONS
                    }
                    for side in ("A", "B")
                },
            }
            for reviewer in ("judge-1", "judge-2")
        ],
    }


def test_private_blind_packet_contains_exact_pair_but_never_verdict(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    candidate = DESTINATION.read_text(encoding="utf-8")
    result = prepare(
        case_id=CASE, candidate_text=candidate,
        destination=packet, seed=b"k" * 32,
    )
    assert result["document_count"] == 2
    assert result["qualified_for_legal_quality"] is False
    assert {p.name for p in packet.iterdir()} == {
        "A.md", "B.md", "case-evidence.json",
        "PRIVATE-mapping.json", "review-form.json"
    }
    assert packet.stat().st_mode & 0o077 == 0
    assert all(f.stat().st_mode & 0o077 == 0 for f in packet.iterdir())
    mapping = json.loads((packet / "PRIVATE-mapping.json").read_text())
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    labels = mapping["labels"]
    assert (packet / (labels["donor"] + ".md")).read_text() == json.loads(
        DONOR.read_text(encoding="utf-8")
    )["document"]
    assert (packet / (labels["candidate"] + ".md")).read_text() == candidate
    source = json.loads((packet / "case-evidence.json").read_text())
    form = json.loads((packet / "review-form.json").read_text())
    assert source["source_scope"] == "frozen_synthetic_judit_parser_facts"
    assert source["facts"] == json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert source["fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert form["source_evidence_sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    assert form["source_evidence_sha256"] == mapping["source_evidence_sha256"]
    with pytest.raises(FileExistsError, match="new, empty"):
        prepare(case_id=CASE, candidate_text=candidate, destination=packet, seed=b"k" * 32)


def test_aggregate_requires_two_independent_complete_reviews(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    prepare(
        case_id=CASE, candidate_text=DESTINATION.read_text(),
        destination=packet, seed=b"y" * 32,
    )
    mapping = json.loads((packet / "PRIVATE-mapping.json").read_text())
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    form = json.loads((packet / "review-form.json").read_text())
    accepted = aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=_scoring(form))
    assert accepted["independent_reviewer_count"] == 2
    assert set(accepted["per_dimension"]) == set(DIMENSIONS)
    assert accepted["population_inference_supported"] is False
    assert accepted["legal_quality_qualified"] is False
    assert accepted["reviewer_identity_independence_verified"] is False
    assert accepted["source_reference_truth_independently_verified"] is False
    assert accepted["candidate_generation_verified_live"] is False
    assert accepted["annotation_completeness_checked"] is True
    assert accepted["source_evidence_sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    assert "Comparison to frozen" not in json.dumps(accepted)
    assert "judge-1" not in json.dumps(accepted)

    one = _scoring(form)
    one["reviews"] = one["reviews"][:1]
    with pytest.raises(ValueError, match="at least two"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=one)

    duplicate = _scoring(form)
    duplicate["reviews"][1]["reviewer_id"] = "judge-1"
    with pytest.raises(ValueError, match="unique"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=duplicate)

    missing = _scoring(form)
    del missing["reviews"][0]["scores"]["A"]["readability"]
    with pytest.raises(ValueError, match="dimensions"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=missing)

    invalid = _scoring(form)
    invalid["reviews"][0]["scores"]["B"]["factual_fidelity"] = True
    with pytest.raises(ValueError, match="integer"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=invalid)

    mismatch = _scoring(form)
    mismatch["case_id"] = "unknown-case"
    with pytest.raises(ValueError, match="same case"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=mismatch)


def test_review_evidence_is_mandatory_and_fail_closed(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    prepare(
        case_id=CASE,
        candidate_text=DESTINATION.read_text(encoding="utf-8"),
        destination=packet,
        seed=b"a" * 32,
    )
    mapping = json.loads((packet / "PRIVATE-mapping.json").read_text())
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    form = json.loads((packet / "review-form.json").read_text())

    bad_digest = _scoring(form)
    bad_digest["source_evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source-evidence digest"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=bad_digest)

    forged_evidence = evidence_bytes.replace(b'"schema_version": 1', b'"schema_version": 2', 1)
    assert forged_evidence != evidence_bytes
    with pytest.raises(ValueError, match="file bytes"):
        aggregate(
            mapping=mapping,
            evidence_bytes=forged_evidence,
            answers=_scoring(form),
        )

    missing_annotation = _scoring(form)
    del missing_annotation["reviews"][0]["annotations"]["A"]["factual_fidelity"]
    with pytest.raises(ValueError, match="four dimensions"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=missing_annotation)

    missing_reason = _scoring(form)
    missing_reason["reviews"][0]["annotations"]["A"]["factual_fidelity"][
        "justification"
    ] = "   "
    with pytest.raises(ValueError, match="nonempty text"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=missing_reason)

    missing_location = _scoring(form)
    missing_location["reviews"][1]["annotations"]["B"]["readability"][
        "document_location"
    ] = ""
    with pytest.raises(ValueError, match="nonempty text"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=missing_location)

    absent_refs = _scoring(form)
    absent_refs["reviews"][0]["annotations"]["A"]["material_omissions"][
        "source_refs"
    ] = []
    with pytest.raises(ValueError, match="source references"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=absent_refs)

    duplicate_refs = _scoring(form)
    duplicate_refs["reviews"][0]["annotations"]["A"]["material_omissions"][
        "source_refs"
    ] = ["movimento:5", "movimento:5"]
    with pytest.raises(ValueError, match="source references"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=duplicate_refs)

    extra_key = _scoring(form)
    extra_key["reviews"][0]["annotations"]["A"]["legal_grounding"][
        "unexpected"
    ] = "not a valid contract field"
    with pytest.raises(ValueError, match="fields incomplete"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, answers=extra_key)
