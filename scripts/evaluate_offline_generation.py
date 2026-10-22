from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.claim_evidence import (
    build_material_claims,
    expected_material_claims,
    movement_evidence_ref,
    process_evidence_ref,
    validate_claim_evidence,
)
from app.claim_verification import verify_material_claims
from app.rag import (
    EMPTY_STEPS_WARNING,
    _generate,
    _secret_summary,
    _serialize_steps,
    _validate_provider_summary,
)
from app.retrieval import DEFAULT_RANK_LIMIT, Step, rank_steps

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "tests/eval/synthetic_cases.json"
DEFAULT_BASELINE = REPO_ROOT / "tests/eval/offline_generation_baseline.json"

_MILESTONE_TEXT = {
    "citacao": "CITAÇÃO realizada",
    "audiencia": "AUDIÊNCIA realizada",
    "sentenca": "SENTENÇA proferida",
    "acordao": "ACÓRDÃO publicado",
    "penhora": "PENHORA registrada",
    "recurso": "RECURSO interposto",
    "transito_em_julgado": "TRÂNSITO EM JULGADO certificado",
    "arquivamento": "ARQUIVAMENTO registrado",
}
_PROCESS_RE = re.compile(r"<processo_json>\n(?P<json>.*?)\n</processo_json>", re.DOTALL)
_STEPS_RE = re.compile(r"<movimentos_json>\n(?P<json>.*?)\n</movimentos_json>", re.DOTALL)


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("offline generation dataset must be a JSON array")
    return payload


def _milestone_text(label: str) -> str:
    try:
        return _MILESTONE_TEXT[label]
    except KeyError as exc:
        raise ValueError(f"unsupported synthetic milestone label: {label}") from exc


def _context(case: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    case_id = str(case["case_id"])
    count = int(case["step_count"])
    code = f"0000000-00.2026.8.21.{int(case_id.rsplit('-', 1)[1]):04d}"
    expected = case.get("expected_milestones", [])
    label = str(expected[0]) if expected else None
    milestone = _milestone_text(label) if label else None

    steps: list[Step] = []
    target = max(1, min(count, count // 2 or 1)) if count else 0
    for number in range(1, count + 1):
        text = milestone if milestone and number == target else f"Movimento ordinário sintético {number}"
        steps.append(
            Step(
                id=uuid5(NAMESPACE_URL, f"rpy:generation:{case_id}:{number}"),
                step_number=number,
                text=text,
                title="Movimento",
                source_step_number=number,
            )
        )

    ranked = rank_steps(
        query=milestone or "movimento processual",
        steps=steps,
        vector_scores=None,
        limit=DEFAULT_RANK_LIMIT,
    )
    context: dict[str, Any] = {
        "synthetic_case_id": case_id,
        "code": code,
        "court": "Tribunal Sintético",
        "class_name": "Procedimento Sintético",
        "subjects": [],
        "parties": [],
        "secrecy_level": int(case.get("secrecy_level", 0)),
        "header": {"instance": int(case.get("instance", 1))},
        "step_count": count,
        "steps": _serialize_steps(ranked),
        "_selected_sources": [
            {
                "evidence_ref": movement_evidence_ref(item.step.id),
                "step_id": item.step.id,
            }
            for item in ranked
        ],
        "_attachment_sources": [],
        "_process_evidence_ref": process_evidence_ref(
            uuid5(NAMESPACE_URL, f"rpy:generation:{case_id}:version")
        ),
    }
    if not steps:
        context["source_warnings"] = [EMPTY_STEPS_WARNING]
    return context, milestone


@dataclass(slots=True)
class _FakeMessages:
    calls: list[dict[str, Any]]

    async def create(self, **request: Any) -> Any:
        self.calls.append(request)
        prompt = str(request["messages"][0]["content"])
        process_match = _PROCESS_RE.search(prompt)
        steps_match = _STEPS_RE.search(prompt)
        if process_match is None or steps_match is None:
            raise AssertionError("provider prompt does not contain structured process/movement payload")

        process = json.loads(process_match.group("json"))
        steps = json.loads(steps_match.group("json"))
        case_id = str(process["synthetic_case_id"])
        correction = "<validation_errors>" in prompt
        force_first_failure = case_id in {"synthetic-01", "synthetic-20"} and not correction

        milestone = next(
            (
                str(step.get("text") or "")
                for step in steps
                if any(token in str(step.get("text") or "") for token in _MILESTONE_TEXT.values())
            ),
            None,
        )
        warnings = process.get("source_warnings") or []
        payload = {
            "synthesis": f"O processo possui {int(process.get('step_count') or 0)} movimentos.",
            "timeline": [f"{milestone}."] if milestone else [],
            "current_status": (
                "Recomendo que a parte tome providências."
                if force_first_failure
                else str((steps[-1] if steps else {}).get("text") or "Sem movimentação processual.")
            ),
            "attention": (
                [str(warning) for warning in warnings]
                if warnings
                else ["Nenhuma divergência objetiva identificada."]
            ),
            "decisions": [],
            "deadlines": [],
            "related_processes": [],
            "attachments": [],
        }
        payload["claims"] = build_material_claims(
            payload,
            evidence_refs=[str(process["evidence_ref"])],
        )
        if steps:
            current_status_ref = str(steps[-1].get("evidence_ref") or "")
            if current_status_ref:
                for claim in payload["claims"]:
                    if claim["claim_id"] == "current_status":
                        claim["evidence_refs"] = [current_status_ref]

        if milestone:
            movement_ref = next(
                (
                    str(step.get("evidence_ref") or "")
                    for step in steps
                    if milestone in str(step.get("text") or "")
                ),
                "",
            )
            if movement_ref:
                for claim in payload["claims"]:
                    if claim["claim_id"] == "timeline:0":
                        claim["evidence_refs"] = [movement_ref]
        text = json.dumps(payload, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


@dataclass(slots=True)
class _FakeClient:
    messages: _FakeMessages

    @classmethod
    def create(cls) -> "_FakeClient":
        return cls(messages=_FakeMessages(calls=[]))


async def evaluate_generation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    public_cases = 0
    final_valid = 0
    anchored_expected = 0
    anchored_present = 0
    forced_retries = 0
    recovered_retries = 0
    secret_cases = 0
    secret_provider_calls = 0
    material_claims = 0
    structurally_unsupported_claims = 0
    verification_counts = {
        "supported": 0,
        "contradicted": 0,
        "insufficient": 0,
        "not_evaluated": 0,
    }
    verification_counts_by_class: dict[str, dict[str, int]] = {}
    per_case: dict[str, dict[str, Any]] = {}

    for case in cases:
        case_id = str(case["case_id"])
        context, milestone = _context(case)
        if int(case.get("secrecy_level", 0)) > 0:
            secret_cases += 1
            before = 0
            summary = _secret_summary(context)
            after = 0
            secret_provider_calls += after - before
            per_case[case_id] = {
                "mode": "secret-local",
                "provider_calls": 0,
                "summary_nonempty": bool(summary.strip()),
            }
            continue

        public_cases += 1
        client = _FakeClient.create()
        first = await _generate(client, context)
        validation = _validate_provider_summary(first, context)
        text = first
        retried = False
        if not validation.passed:
            retried = True
            forced_retries += 1
            text = await _generate(client, context, validation.errors)
            validation = _validate_provider_summary(text, context)
            if validation.passed:
                recovered_retries += 1

        claim_total = 0
        claim_unsupported = 0
        case_verification_counts = {
            "supported": 0,
            "contradicted": 0,
            "insufficient": 0,
            "not_evaluated": 0,
        }
        parsed = context.get("_parsed_summary")
        if isinstance(parsed, dict):
            expected_claims = expected_material_claims(parsed)
            claim_total = len(expected_claims)
            material_claims += claim_total
            validated_claims, claim_errors = validate_claim_evidence(parsed, context)
            supported_ids = {
                claim.claim_id
                for claim in validated_claims
                if claim.evidence_refs
                and claim.text == expected_claims.get(claim.claim_id)
            }
            claim_unsupported = claim_total - len(supported_ids)
            if claim_errors and claim_unsupported == 0:
                claim_unsupported = claim_total
            structurally_unsupported_claims += claim_unsupported

            if not claim_errors:
                verification = verify_material_claims(validated_claims, context)
                for claim in validated_claims:
                    result = verification[claim.claim_id]
                    verification_counts[result.status] += 1
                    case_verification_counts[result.status] += 1
                    class_counts = verification_counts_by_class.setdefault(
                        claim.claim_class,
                        {
                            "supported": 0,
                            "contradicted": 0,
                            "insufficient": 0,
                            "not_evaluated": 0,
                        },
                    )
                    class_counts[result.status] += 1

        if validation.passed:
            final_valid += 1
        if milestone and context["steps"]:
            anchored_expected += 1
            if milestone in text:
                anchored_present += 1

        per_case[case_id] = {
            "mode": "provider-fake",
            "provider_calls": len(client.messages.calls),
            "retried": retried,
            "validation_passed": validation.passed,
            "validation_errors": validation.errors,
            "anchored_milestone_present": (
                milestone in text if milestone and context["steps"] else None
            ),
            "material_claims": claim_total,
            "structurally_unsupported_claims": claim_unsupported,
            "claim_verification_counts": case_verification_counts,
        }

    return {
        "cases": len(cases),
        "metrics": {
            "final_validation_pass_rate": final_valid / public_cases if public_cases else 1.0,
            "anchored_milestone_presence_rate": (
                anchored_present / anchored_expected if anchored_expected else 1.0
            ),
            "corrective_retry_recovery_rate": (
                recovered_retries / forced_retries if forced_retries else 1.0
            ),
            "secret_provider_call_rate": (
                secret_provider_calls / secret_cases if secret_cases else 0.0
            ),
            "structural_unsupported_claim_rate": (
                structurally_unsupported_claims / material_claims
                if material_claims
                else 0.0
            ),
            "deterministic_supported_claim_rate": (
                verification_counts["supported"] / material_claims
                if material_claims
                else 0.0
            ),
            "semantic_unverified_claim_rate": (
                (
                    verification_counts["contradicted"]
                    + verification_counts["insufficient"]
                    + verification_counts["not_evaluated"]
                )
                / material_claims
                if material_claims
                else 0.0
            ),
        },
        "counts": {
            "public_cases": public_cases,
            "final_valid": final_valid,
            "anchored_expected": anchored_expected,
            "anchored_present": anchored_present,
            "forced_retries": forced_retries,
            "recovered_retries": recovered_retries,
            "secret_cases": secret_cases,
            "secret_provider_calls": secret_provider_calls,
            "material_claims": material_claims,
            "structurally_unsupported_claims": structurally_unsupported_claims,
            "claim_verification": verification_counts,
            "claim_verification_by_class": verification_counts_by_class,
        },
        "case_metrics": per_case,
    }


def load_baseline(path: Path = DEFAULT_BASELINE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("offline generation baseline must be a JSON object")
    return payload


def compare_to_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for metric, contract in baseline["metrics"].items():
        direction = str(contract["direction"])
        threshold = float(contract["value"])
        actual = float(report["metrics"][metric])
        failed = actual < threshold if direction == "min" else actual > threshold
        if failed:
            failures.append(
                {
                    "metric": metric,
                    "direction": direction,
                    "threshold": threshold,
                    "actual": actual,
                }
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise Rpy generation and validation with a deterministic local provider fake"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--check-baseline", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(evaluate_generation(load_dataset(args.dataset)))
    failures: list[dict[str, Any]] = []
    if args.check_baseline:
        baseline = load_baseline(args.baseline)
        failures = compare_to_baseline(report, baseline)
        report["baseline_check"] = {
            "baseline_version": baseline.get("baseline_version"),
            "passed": not failures,
            "failures": failures,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
