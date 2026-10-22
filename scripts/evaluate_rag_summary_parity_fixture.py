"""Locally inspect the supplied reference fixture without printing case data."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.judit import extract_promotable_fields
from app.rag import _movement_temporal_facts

FIXTURE_MEMBER = "rag_resumo/tests/fixtures/judit_exemplo.json"


def _lawsuit_sources(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("fixture root must be an object")
    if isinstance(payload.get("page_data"), list):
        return [
            item["response_data"]
            for item in payload["page_data"]
            if isinstance(item, dict)
            and item.get("response_type") == "lawsuit"
            and isinstance(item.get("response_data"), dict)
        ]
    if payload.get("response_type") == "lawsuit":
        source = payload.get("response_data")
        return [source] if isinstance(source, dict) else []
    return [payload] if "code" in payload and "parties" in payload else []


def evaluate(archive: Path) -> dict[str, Any]:
    archive_bytes = archive.read_bytes()
    with zipfile.ZipFile(archive) as source_zip:
        payload = json.loads(source_zip.read(FIXTURE_MEMBER))

    sources = _lawsuit_sources(payload)
    totals = {
        "input_cases": len(sources),
        "party_count": 0,
        "representative_count": 0,
        "subject_count": 0,
        "movement_count": 0,
        "scheduled_hearing_annotation_count": 0,
        "explicit_period_annotation_count": 0,
    }
    for source in sources:
        projection = extract_promotable_fields(source)
        totals["party_count"] += len(projection["parties"])
        totals["representative_count"] += len(projection["representatives"])
        totals["subject_count"] += len(projection["subjects"])
        totals["movement_count"] += len(projection["steps"])
        for movement in projection["steps"]:
            facts = _movement_temporal_facts(movement["text"])
            totals["scheduled_hearing_annotation_count"] += int(
                "scheduled_hearing" in facts
            )
            totals["explicit_period_annotation_count"] += int(
                "explicit_period" in facts
            )

    return {
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "fixture_member": FIXTURE_MEMBER,
        "evidence_type": "supplied-fixture-local-inspection-no-provider-calls",
        **totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.archive), sort_keys=True))


if __name__ == "__main__":
    main()
