from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.blind_paired_legal_review import DIMENSIONS, aggregate, prepare
from scripts.evaluate_paired_legal_corpus import DONOR
from scripts.evaluate_writing_parity import DESTINATION


CASE = "m01-doc01-scheduled-hearing"


def _scoring(form: dict) -> dict:
    return {
        **form,
        "reviews": [
            {"reviewer_id": "judge-1", "scores": {
                "A": {key: 2 for key in DIMENSIONS},
                "B": {key: 3 for key in DIMENSIONS},
            }},
            {"reviewer_id": "judge-2", "scores": {
                "A": {key: 2 for key in DIMENSIONS},
                "B": {key: 3 for key in DIMENSIONS},
            }},
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
        "A.md", "B.md", "PRIVATE-mapping.json", "review-form.json"
    }
    assert packet.stat().st_mode & 0o077 == 0
    assert all(f.stat().st_mode & 0o077 == 0 for f in packet.iterdir())
    labels = json.loads((packet / "PRIVATE-mapping.json").read_text())["labels"]
    assert (packet / (labels["donor"] + ".md")).read_text() == json.loads(
        DONOR.read_text(encoding="utf-8")
    )["document"]
    assert (packet / (labels["candidate"] + ".md")).read_text() == candidate
    with pytest.raises(FileExistsError, match="new, empty"):
        prepare(case_id=CASE, candidate_text=candidate, destination=packet, seed=b"k" * 32)


def test_aggregate_requires_two_independent_complete_reviews(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    prepare(
        case_id=CASE, candidate_text=DESTINATION.read_text(),
        destination=packet, seed=b"y" * 32,
    )
    mapping = json.loads((packet / "PRIVATE-mapping.json").read_text())
    form = json.loads((packet / "review-form.json").read_text())
    accepted = aggregate(mapping=mapping, answers=_scoring(form))
    assert accepted["independent_reviewer_count"] == 2
    assert set(accepted["per_dimension"]) == set(DIMENSIONS)
    assert accepted["population_inference_supported"] is False
    assert accepted["legal_quality_qualified"] is False
    assert "judge-1" not in json.dumps(accepted)

    one = _scoring(form)
    one["reviews"] = one["reviews"][:1]
    with pytest.raises(ValueError, match="at least two"):
        aggregate(mapping=mapping, answers=one)

    duplicate = _scoring(form)
    duplicate["reviews"][1]["reviewer_id"] = "judge-1"
    with pytest.raises(ValueError, match="unique"):
        aggregate(mapping=mapping, answers=duplicate)

    missing = _scoring(form)
    del missing["reviews"][0]["scores"]["A"]["readability"]
    with pytest.raises(ValueError, match="dimensions"):
        aggregate(mapping=mapping, answers=missing)

    invalid = _scoring(form)
    invalid["reviews"][0]["scores"]["B"]["factual_fidelity"] = True
    with pytest.raises(ValueError, match="integer"):
        aggregate(mapping=mapping, answers=invalid)

    mismatch = _scoring(form)
    mismatch["case_id"] = "unknown-case"
    with pytest.raises(ValueError, match="same case"):
        aggregate(mapping=mapping, answers=mismatch)
