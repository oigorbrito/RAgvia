from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.blind_paired_legal_review import DIMENSIONS, aggregate, prepare
from scripts.evaluate_paired_legal_corpus import DONOR
from scripts.evaluate_writing_parity import DESTINATION, EXPECTED, FIXTURE


CASE = "m01-doc01-scheduled-hearing"


def _private_mapping_path(tmp_path: Path) -> Path:
    private_dir = tmp_path / "coordinator-private"
    private_dir.mkdir(mode=0o700, exist_ok=True)
    return private_dir / "PRIVATE-mapping.json"


def _documents(packet: Path) -> dict[str, bytes]:
    return {side: (packet / f"{side}.md").read_bytes() for side in ("A", "B")}


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
                            "document_location": "linha:1",
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
        destination=packet, private_mapping=_private_mapping_path(tmp_path), seed=b"k" * 32,
    )
    assert result["document_count"] == 2
    assert result["qualified_for_legal_quality"] is False
    assert {p.name for p in packet.iterdir()} == {
        "A.md", "B.md", "case-evidence.json",
        "review-form.json"
    }
    assert not (packet / "PRIVATE-mapping.json").exists()
    if os.name != "nt":
        assert packet.stat().st_mode & 0o077 == 0
        assert all(f.stat().st_mode & 0o077 == 0 for f in packet.iterdir())
        assert _private_mapping_path(tmp_path).stat().st_mode & 0o077 == 0
    mapping = json.loads(_private_mapping_path(tmp_path).read_text(encoding="utf-8"))
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    labels = mapping["labels"]
    assert (packet / (labels["donor"] + ".md")).read_text() == json.loads(
        DONOR.read_text(encoding="utf-8")
    )["document"]
    assert (packet / (labels["candidate"] + ".md")).read_text(encoding="utf-8") == candidate
    # Bytes are canonical UTF-8, never platform-translated CRLF.
    assert _documents(packet)[labels["candidate"]] == candidate.encode("utf-8")
    assert _documents(packet)[labels["donor"]] == json.loads(
        DONOR.read_text(encoding="utf-8")
    )["document"].encode("utf-8")
    assert all(b"\r\n" not in raw for raw in _documents(packet).values())
    source = json.loads((packet / "case-evidence.json").read_text())
    form = json.loads((packet / "review-form.json").read_text())
    assert source["source_scope"] == "frozen_synthetic_judit_parser_facts"
    assert source["facts"] == json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert source["fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert evidence_bytes == (json.dumps(source, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert form["source_evidence_sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    assert form["source_evidence_sha256"] == mapping["source_evidence_sha256"]
    hashes = mapping["document_sha256_by_side"]
    assert set(hashes) == {"A", "B"}
    assert all(
        hashes[side] == hashlib.sha256(_documents(packet)[side]).hexdigest()
        for side in ("A", "B")
    )
    with pytest.raises(FileExistsError, match="already exists|new, empty"):
        prepare(case_id=CASE, candidate_text=candidate, destination=packet, private_mapping=_private_mapping_path(tmp_path), seed=b"k" * 32)


def test_aggregate_requires_two_independent_complete_reviews(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    prepare(
        case_id=CASE, candidate_text=DESTINATION.read_text(),
        destination=packet, private_mapping=_private_mapping_path(tmp_path), seed=b"y" * 32,
    )
    mapping = json.loads(_private_mapping_path(tmp_path).read_text(encoding="utf-8"))
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    form = json.loads((packet / "review-form.json").read_text())
    accepted = aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=_scoring(form))
    assert accepted["independent_reviewer_count"] == 2
    assert set(accepted["per_dimension"]) == set(DIMENSIONS)
    assert accepted["population_inference_supported"] is False
    assert accepted["legal_quality_qualified"] is False
    assert accepted["reviewer_identity_independence_verified"] is False
    assert accepted["source_reference_truth_independently_verified"] is False
    assert accepted["candidate_generation_verified_live"] is False
    assert accepted["annotation_completeness_checked"] is True
    assert accepted["blinded_document_integrity_checked"] is True
    assert accepted["source_reference_ids_resolved"] is True
    assert accepted["document_location_bounds_checked"] is True
    assert accepted["source_evidence_sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    assert "Comparison to frozen" not in json.dumps(accepted)
    assert "judge-1" not in json.dumps(accepted)

    one = _scoring(form)
    one["reviews"] = one["reviews"][:1]
    with pytest.raises(ValueError, match="at least two"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=one)

    duplicate = _scoring(form)
    duplicate["reviews"][1]["reviewer_id"] = "judge-1"
    with pytest.raises(ValueError, match="unique"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=duplicate)

    missing = _scoring(form)
    del missing["reviews"][0]["scores"]["A"]["readability"]
    with pytest.raises(ValueError, match="dimensions"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=missing)

    invalid = _scoring(form)
    invalid["reviews"][0]["scores"]["B"]["factual_fidelity"] = True
    with pytest.raises(ValueError, match="integer"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=invalid)

    mismatch = _scoring(form)
    mismatch["case_id"] = "unknown-case"
    with pytest.raises(ValueError, match="same case"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=mismatch)


def test_review_evidence_is_mandatory_and_fail_closed(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    prepare(
        case_id=CASE,
        candidate_text=DESTINATION.read_text(encoding="utf-8"),
        destination=packet,
        private_mapping=_private_mapping_path(tmp_path),
        seed=b"a" * 32,
    )
    mapping = json.loads(_private_mapping_path(tmp_path).read_text(encoding="utf-8"))
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    form = json.loads((packet / "review-form.json").read_text())

    bad_digest = _scoring(form)
    bad_digest["source_evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source-evidence digest"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=bad_digest)

    forged_evidence = evidence_bytes.replace(b'"schema_version": 1', b'"schema_version": 2', 1)
    assert forged_evidence != evidence_bytes
    with pytest.raises(ValueError, match="file bytes"):
        aggregate(
            mapping=mapping,
            evidence_bytes=forged_evidence,
            documents=_documents(packet),
            answers=_scoring(form),
        )

    missing_annotation = _scoring(form)
    del missing_annotation["reviews"][0]["annotations"]["A"]["factual_fidelity"]
    with pytest.raises(ValueError, match="four dimensions"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=missing_annotation)

    missing_reason = _scoring(form)
    missing_reason["reviews"][0]["annotations"]["A"]["factual_fidelity"][
        "justification"
    ] = "   "
    with pytest.raises(ValueError, match="nonempty text"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=missing_reason)

    missing_location = _scoring(form)
    missing_location["reviews"][1]["annotations"]["B"]["readability"][
        "document_location"
    ] = ""
    with pytest.raises(ValueError, match="nonempty text"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=missing_location)

    absent_refs = _scoring(form)
    absent_refs["reviews"][0]["annotations"]["A"]["material_omissions"][
        "source_refs"
    ] = []
    with pytest.raises(ValueError, match="source references"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=absent_refs)

    duplicate_refs = _scoring(form)
    duplicate_refs["reviews"][0]["annotations"]["A"]["material_omissions"][
        "source_refs"
    ] = ["movimento:5", "movimento:5"]
    with pytest.raises(ValueError, match="source references"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=duplicate_refs)

    extra_key = _scoring(form)
    extra_key["reviews"][0]["annotations"]["A"]["legal_grounding"][
        "unexpected"
    ] = "not a valid contract field"
    with pytest.raises(ValueError, match="fields incomplete"):
        aggregate(mapping=mapping, evidence_bytes=evidence_bytes, documents=_documents(packet), answers=extra_key)


def test_blinded_document_and_reference_attacks_are_rejected(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    original = DESTINATION.read_text(encoding="utf-8")
    prepare(case_id=CASE, candidate_text=original, destination=packet, private_mapping=_private_mapping_path(tmp_path), seed=b"z" * 32)
    mapping = json.loads(_private_mapping_path(tmp_path).read_text(encoding="utf-8"))
    evidence_bytes = (packet / "case-evidence.json").read_bytes()
    form = json.loads((packet / "review-form.json").read_text())

    def check(answers: dict, documents: dict[str, bytes] | None = None) -> dict:
        return aggregate(
            mapping=mapping, answers=answers, evidence_bytes=evidence_bytes,
            documents=_documents(packet) if documents is None else documents,
        )

    clean = check(_scoring(form))
    assert clean["source_reference_ids_resolved"]
    assert clean["document_location_bounds_checked"]
    assert clean["blinded_document_integrity_checked"]
    assert clean["legal_quality_qualified"] is False

    mutated_docs = _documents(packet)
    mutated_docs["A"] += b"\nAlteracao apos cegamento\n"
    with pytest.raises(ValueError, match="bytes differ"):
        check(_scoring(form), mutated_docs)

    mutated_docs = _documents(packet)
    mutated_docs.pop("B")
    with pytest.raises(ValueError, match="both original"):
        check(_scoring(form), mutated_docs)

    fake_source = _scoring(form)
    fake_source["reviews"][0]["annotations"]["A"]["factual_fidelity"][
        "source_refs"
    ] = ["movimento:999"]
    with pytest.raises(ValueError, match="nonexistent"):
        check(fake_source)

    changed_source_type = _scoring(form)
    changed_source_type["reviews"][0]["annotations"]["A"]["factual_fidelity"][
        "source_refs"
    ] = ["movimento:inexistente"]
    with pytest.raises(ValueError, match="nonexistent"):
        check(changed_source_type)

    out_of_bounds = _scoring(form)
    out_of_bounds["reviews"][1]["annotations"]["B"]["readability"][
        "document_location"
    ] = "linha:999999"
    with pytest.raises(ValueError, match="outside issued"):
        check(out_of_bounds)

    vague_location = _scoring(form)
    vague_location["reviews"][1]["annotations"]["B"]["readability"][
        "document_location"
    ] = "Movimentações section"
    with pytest.raises(ValueError, match="linha:N"):
        check(vague_location)

    legitimate_source = _scoring(form)
    legitimate_source["reviews"][0]["annotations"]["B"]["material_omissions"][
        "source_refs"
    ] = ["parte:p1", "classe:c1", "assunto:a1", "campo:cnj"]
    assert check(legitimate_source)["source_reference_ids_resolved"]


def test_prepare_rejects_materially_invalid_candidate_before_creating_review(tmp_path: Path) -> None:
    baseline = DESTINATION.read_text(encoding="utf-8")
    assert "Pessoa Sintética Alfa" in baseline
    bad = baseline.replace("Pessoa Sintética Alfa", "Parte omitida")
    packet = tmp_path / "invalid"
    with pytest.raises(ValueError, match="deterministic writing gate"):
        prepare(case_id=CASE, candidate_text=bad, destination=packet, private_mapping=_private_mapping_path(tmp_path), seed=b"z" * 32)
    assert not packet.exists()



def test_private_mapping_cannot_be_delivered_or_overwritten(tmp_path: Path) -> None:
    candidate = DESTINATION.read_text(encoding="utf-8")
    packet = tmp_path / "reviewer-packet"
    with pytest.raises(ValueError, match="outside reviewer packet"):
        prepare(
            case_id=CASE, candidate_text=candidate, destination=packet,
            private_mapping=packet / "PRIVATE-mapping.json", seed=b"z" * 32,
        )
    assert not packet.exists()

    private_mapping = _private_mapping_path(tmp_path)
    private_mapping.write_text("previous-coordinator-secret", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        prepare(
            case_id=CASE, candidate_text=candidate, destination=packet,
            private_mapping=private_mapping, seed=b"z" * 32,
        )
    assert not packet.exists()
    assert private_mapping.read_text(encoding="utf-8") == "previous-coordinator-secret"
