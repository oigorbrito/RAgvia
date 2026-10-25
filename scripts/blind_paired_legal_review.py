"""Prepare private A/B review packets and aggregate paired human judgements.

No provider calls. Mapping is local-only and must never be uploaded to CI.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from scripts.evaluate_paired_legal_corpus import DONOR, HUMAN_LABELS, evaluate_case

DIMENSIONS = tuple(HUMAN_LABELS)
SIDES = ("A", "B")


def _write(path: Path, value: Any, *, as_text: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value if as_text else json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare(
    *, case_id: str, candidate_text: str, destination: Path,
    candidate_commit: str | None = None, seed: bytes | None = None,
) -> dict[str, Any]:
    record = evaluate_case(
        case_id=case_id, document=candidate_text, commit_sha=candidate_commit
    )
    donor_text = json.loads(DONOR.read_text(encoding="utf-8"))["document"]
    if not isinstance(donor_text, str) or not donor_text.strip() or not candidate_text.strip():
        raise ValueError("both paired documents must be nonempty")
    if destination.is_symlink():
        raise ValueError("review destination must not be a symlink")
    if not destination.exists():
        destination.mkdir(parents=True, mode=0o700)
    if destination.stat().st_mode & 0o077:
        raise PermissionError("review destination must have private permissions (0700)")
    if any(destination.iterdir()):
        raise FileExistsError("review packet must use a new, empty directory")
    randomness = seed if seed is not None else secrets.token_bytes(32)
    if len(randomness) < 16:
        raise ValueError("blinding seed too short")
    blind_token = hmac.digest(randomness, case_id.encode("utf-8"), "sha256")
    candidate_side = "A" if blind_token[0] & 1 else "B"
    donor_side = "B" if candidate_side == "A" else "A"
    for side, value in ((donor_side, donor_text), (candidate_side, candidate_text)):
        _write(destination / (side + ".md"), value, as_text=True)
    mapping = {
        "schema_version": 1,
        "case_id": case_id,
        "blind_id": hashlib.sha256(blind_token).hexdigest()[:24],
        "donor_commit": record["donor_commit"],
        "candidate_commit": candidate_commit,
        "document_sha256": record["candidate_document_sha256"],
        "labels": {"donor": donor_side, "candidate": candidate_side},
    }
    _write(destination / "PRIVATE-mapping.json", mapping)
    _write(destination / "review-form.json", {
        "schema_version": 1,
        "case_id": case_id,
        "blind_id": mapping["blind_id"],
        "rubric": HUMAN_LABELS,
        "instructions": "Two independent reviewers score both A and B: 0 critical, 1 material issue, 2 acceptable with caveats, 3 adequate. Review the case evidence, not just the donor document.",
        "reviews": [],
    })
    return {"blind_id": mapping["blind_id"], "document_count": 2,
            "review_count": 0, "qualified_for_legal_quality": False}


def aggregate(*, mapping: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    if (mapping.get("schema_version") != 1 or answers.get("schema_version") != 1
            or mapping.get("case_id") != answers.get("case_id")
            or mapping.get("blind_id") != answers.get("blind_id")):
        raise ValueError("blind mapping and reviews are not for the same case")
    labels = mapping.get("labels")
    if not isinstance(labels, dict) or set(labels.values()) != set(SIDES):
        raise ValueError("invalid blinded A/B mapping")
    reviews = answers.get("reviews")
    if not isinstance(reviews, list) or len(reviews) < 2:
        raise ValueError("at least two independent reviewers required")
    identities = set()
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("invalid review")
        rid = review.get("reviewer_id")
        if not isinstance(rid, str) or not rid.strip() or rid.strip() in identities:
            raise ValueError("reviewer identifiers must be unique")
        identities.add(rid.strip())
        scores = review.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(SIDES):
            raise ValueError("both blinded summaries must be scored")
        for side in SIDES:
            row = scores[side]
            if not isinstance(row, dict) or set(row) != set(DIMENSIONS):
                raise ValueError("all four rubric dimensions must be scored")
            if any(type(value) is not int or not 0 <= value <= 3 for value in row.values()):
                raise ValueError("scores must be integer values from zero to three")
    by_dimension = {}
    for dimension in DIMENSIONS:
        differences = [
            rev["scores"][labels["candidate"]][dimension]
            - rev["scores"][labels["donor"]][dimension]
            for rev in reviews
        ]
        direction = [1 if x > 0 else -1 if x < 0 else 0 for x in differences]
        by_dimension[dimension] = {
            "paired_differences": differences,
            "direction_agreement": len(set(direction)) == 1,
            "unanimous_better": all(x > 0 for x in differences),
            "unanimous_worse": all(x < 0 for x in differences),
        }
    return {
        "schema_version": 1,
        "case_id": mapping["case_id"],
        "blind_id": mapping["blind_id"],
        "independent_reviewer_count": len(identities),
        "independent_paired_cases": 1,
        "per_dimension": by_dimension,
        "population_inference_supported": False,
        "legal_quality_qualified": False,
        "release_qualified": False,
    }


def main() -> int:
    cli = argparse.ArgumentParser()
    modes = cli.add_subparsers(dest="operation", required=True)
    make = modes.add_parser("prepare")
    make.add_argument("--case-id", required=True)
    make.add_argument("--candidate", type=Path, required=True)
    make.add_argument("--commit")
    make.add_argument("--out", type=Path, required=True)
    collect = modes.add_parser("aggregate")
    collect.add_argument("--mapping", type=Path, required=True)
    collect.add_argument("--answers", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.operation == "prepare":
        result = prepare(
            case_id=args.case_id,
            candidate_text=args.candidate.read_text(encoding="utf-8"),
            destination=args.out,
            candidate_commit=args.commit,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    answers = json.loads(args.answers.read_text(encoding="utf-8"))
    result = aggregate(mapping=mapping, answers=answers)
    _write(args.output, result)
    print(json.dumps({
        "case_id": result["case_id"],
        "reviewers": result["independent_reviewer_count"],
        "legal_quality_qualified": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
