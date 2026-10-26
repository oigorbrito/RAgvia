"""Re-execute the original frozen donor and compare its real output to the oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.capture_donor_writing_reference import (
    DONOR_COMMIT,
    DEFAULT_OUTPUT,
    FIXTURE,
    capture,
)


def verify_reference(*, donor_path: Path, reference_path: Path = DEFAULT_OUTPUT) -> dict:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    actual = capture(donor_path)
    if reference["provenance"]["commit"] != DONOR_COMMIT:
        raise ValueError("recorded donor commit differs from frozen revision")
    if actual["provenance"] != reference["provenance"]:
        raise ValueError("donor provenance/input differs from recorded oracle")
    if actual["output"] != reference["output"]:
        raise ValueError("original donor generator did not reproduce saved sections")
    if actual["document"] != reference["document"]:
        raise ValueError("original donor renderer did not reproduce saved document")
    return {
        "donor_commit": DONOR_COMMIT,
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "document_sha256": hashlib.sha256(actual["document"].encode("utf-8")).hexdigest(),
        "reproduced": True,
        "paid_provider_calls": 0,
        "live_legal_quality_qualified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--donor-path", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_reference(donor_path=args.donor_path, reference_path=args.reference)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
