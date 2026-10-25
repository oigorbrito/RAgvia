"""FUNC05 regression contracts for literal RagResumo alerts.

The donor's alerts are lexical observations, not authority to alter process data.
This test keeps the original safe boundary while fallback publication is reviewed.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.judit import extract_promotable_fields
from app.rag import _provider_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "judit" / "parser_parity_synthetic.json"


def test_donor_cadastro_alert_does_not_rewrite_source_or_provider_context() -> None:
    original = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = extract_promotable_fields(original)
    source_parser = result["header"]["source_parser"]
    assert source_parser["indicators"], "The donor synthetic fixture must exercise actual alerts"
    assert all(indicator["kind"] == "heuristic" for indicator in source_parser["indicators"])
    assert source_parser["semantics"] == "lexical_annotations_not_verified_facts"

    # The suspect original area is retained as registered; lexical advice must
    # never overwrite the authoritative field or become an LLM instruction.
    assert result["header"]["area"] == "DIREITO DO TRABALHO"
    process, movements = _provider_payload(result)
    provider_text = json.dumps([process, movements], ensure_ascii=False)
    assert "source_parser" not in provider_text
    assert "lexical_annotations_not_verified_facts" not in provider_text
    for indicator in source_parser["indicators"]:
        assert indicator["message"] not in provider_text


def test_suspect_secondary_class_is_annotation_not_canonical_class() -> None:
    result = extract_promotable_fields(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    classifications = result["header"]["source_parser"]["classifications"]
    suspects = [item for item in classifications if item["suspect"]]
    assert suspects, "Fixture must contain a donor-flagged class"
    assert result["class_name"] == classifications[0]["name"]
    assert all(item["name"] != result["class_name"] for item in suspects)
