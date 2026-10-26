"""Exact-input paired legal-summary pilot; metadata only, no model calls.

One authenticated donor output exists. Mutations check evaluator sensitivity,
NOT additional independent cases. Human legal-quality ratings remain pending.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.evaluate_writing_parity import DONOR, EXPECTED, FIXTURE, evaluate

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/eval/paired_legal_corpus_v1.json"
DIMENSIONS = {
    "Identificação CNJ": "factual_fidelity",
    "Partes e polos explicitamente cadastrados": "factual_fidelity",
    "Relações parte–representante registradas": "factual_fidelity",
    "Classe principal": "factual_fidelity",
    "Cobertura dos assuntos cadastrados": "material_omissions",
    "Classe secundária marcada como suspeita": "factual_fidelity",
    "Alegações e responsabilidade não inferidas apenas pelo polo": "legal_grounding",
    "Explicações de classe/assunto sustentadas por conteúdo recuperado": "legal_grounding",
    "Data do ato separada de data/horário agendado": "factual_fidelity",
    "Marcos e agrupamento administrativo sem perda de eventos": "material_omissions",
    "Ordem cronológica dos movimentos": "factual_fidelity",
    "Informação ausente sem afirmar ocorrência": "legal_grounding",
    "Ausência de documentos pessoais no documento publicado": "factual_fidelity",
}
HUMAN_LABELS = {
    "factual_fidelity": "Partes, atos e temporalidade sem atribuições falsas",
    "material_omissions": "Ausência de omissões processuais relevantes",
    "legal_grounding": "Conclusões jurídicas sustentadas em fontes válidas",
    "readability": "Texto claro, preciso e inteligível para o destinatário",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def revision_metadata(executed_commit: str | None) -> dict[str, Any]:
    """Distinguish PR source HEAD from the commit actually checked out by CI.

    On pull_request events, actions/checkout defaults to a synthetic merge
    commit. Recording GITHUB_SHA as the candidate HEAD would misstate the
    provenance of the published integration evidence.
    """
    candidate_commit = executed_commit
    if executed_commit and (event_path := os.getenv("GITHUB_EVENT_PATH")):
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ValueError("invalid GitHub event payload")
        request = event.get("pull_request")
        if request is not None:
            head = request.get("head") if isinstance(request, dict) else None
            candidate_commit = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(candidate_commit, str) or not re.fullmatch(
                r"[0-9a-fA-F]{40}", candidate_commit
            ):
                raise ValueError("pull request event has no valid head SHA")
    return {
        "candidate_commit": candidate_commit,
        "executed_checkout_commit": executed_commit,
        "executed_exact_candidate_head": (
            candidate_commit == executed_commit
            if candidate_commit is not None and executed_commit is not None
            else None
        ),
    }


def frozen_case(case_id: str) -> dict[str, Any]:
    manifest = json.loads(CORPUS.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unexpected paired corpus schema")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("paired corpus is empty")
    if len({entry["case_id"] for entry in cases}) != len(cases):
        raise ValueError("duplicate paired case id")
    entry = next((item for item in cases if item["case_id"] == case_id), None)
    if entry is None:
        raise ValueError("case has no authentic frozen donor reference")
    donor_bytes = DONOR.read_bytes()
    fixture_hash = sha256(FIXTURE.read_bytes())
    donor = json.loads(donor_bytes)
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    if fixture_hash != entry["fixture_sha256"]:
        raise ValueError("candidate input has drifted from paired fixture")
    if git_blob_sha1(donor_bytes) != entry["donor_oracle_blob_sha1"]:
        raise ValueError("pinned donor output blob has changed")
    if (donor["provenance"]["commit"] != entry["donor_commit"]
            or donor["provenance"]["fixture_sha256"] != fixture_hash):
        raise ValueError("donor provenance does not match candidate input")
    if expected["cnj"] != entry["synthetic_cnj"]:
        raise ValueError("case identity differs from paired input")
    return entry


def source_metadata(claims: list[dict[str, Any]] | None) -> dict[str, Any]:
    if claims is None:
        return {"state": "not_supplied", "total": None}
    if not isinstance(claims, list) or any(not isinstance(c, dict) for c in claims):
        raise ValueError("claim evidence must be a JSON list of objects")
    statuses = Counter(str(c.get("verification_status")) for c in claims)
    no_source = 0
    for c in claims:
        sources = c.get("sources")
        if not isinstance(sources, list) or not any(
            isinstance(s, dict) and s.get("evidence_ref")
            and s.get("source_kind") in {"process", "movement", "attachment"}
            for s in sources
        ):
            no_source += 1
    return {
        "state": "application_verification_metadata",
        "total": len(claims),
        "supported": statuses["supported"],
        "not_supported": len(claims) - statuses["supported"],
        "without_valid_source_reference": no_source,
        "semantic_entailment_proven": False,
    }


def evaluate_case(
    *, case_id: str, document: str,
    claims: list[dict[str, Any]] | None = None, commit_sha: str | None = None,
) -> dict[str, Any]:
    entry = frozen_case(case_id)
    result = evaluate(destination_text=document)
    rows = result["results"]
    if len(rows) != len(DIMENSIONS) or set(row["requirement"] for row in rows) != set(DIMENSIONS):
        raise ValueError("paired rubric does not cover all fixed requirements")
    dimensions: dict[str, list[dict[str, Any]]] = {key: [] for key in HUMAN_LABELS}
    for row in rows:
        dimensions[DIMENSIONS[row["requirement"]]].append({
            "criterion": row["requirement"], "state": row["status"],
            "divergence_documented": bool(row.get("justification")),
        })
    missing = [row["requirement"] for row in rows if row["status"] == "FAIL_DESTINATION_OMISSION"]
    lower = document.casefold()
    # Known negative examples: sentinels, not a comprehensive legal NLI evaluator.
    risks: list[str] = []
    if re.search(r"audi[eê]ncia\s+(?:foi\s+)?(?:realizada|ocorreu|aconteceu)", lower):
        risks.append("hearing_only_scheduled_but_claimed_held")
    if "afirma ter sofrido algum prejuízo" in lower or "responsável pelos fatos" in lower:
        risks.append("merits_inferred_from_party_role")
    provenance = source_metadata(claims)
    return {
        "schema_version": 1,
        "case_id": case_id,
        "sample_type": "single_paired_synthetic_reference",
        "fixture_sha256": entry["fixture_sha256"],
        "donor_commit": entry["donor_commit"],
        "donor_oracle_blob_sha1": entry["donor_oracle_blob_sha1"],
        **revision_metadata(commit_sha),
        "candidate_document_sha256": sha256(document.encode("utf-8")),
        "comparison": {
            "basis": "original_donor_template_vs_runtime_api_with_mocked_model",
            "deterministic_preservation_passed": result["deterministic_preservation_passed"],
            "writing_rubric_passed": result["writing_rubric_passed"],
            "covered": result["rubric_covered"],
            "total": result["rubric_count"],
            "unresolved": missing,
            "dimensions": dimensions,
        },
        "narrow_risk_signals": risks,
        "claim_source_metadata": provenance,
        "human_review": {
            dimension: {
                "state": "pending", "score_0_to_3": None, "criterion": label
            } for dimension, label in HUMAN_LABELS.items()
        },
        "live_model_evaluated": False,
        "legal_quality_qualified": False,
        "release_qualified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--claims", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    claims = json.loads(args.claims.read_text(encoding="utf-8")) if args.claims else None
    report = evaluate_case(
        case_id=args.case_id,
        document=args.destination.read_text(encoding="utf-8"),
        claims=claims, commit_sha=os.getenv("GITHUB_SHA"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "case_id": report["case_id"],
        "deterministic_pass": report["comparison"]["deterministic_preservation_passed"],
        "writing_gaps": report["comparison"]["unresolved"],
        "legal_quality_qualified": False,
    }, ensure_ascii=False))
    return int(
        not report["comparison"]["deterministic_preservation_passed"]
        or bool(report["narrow_risk_signals"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
