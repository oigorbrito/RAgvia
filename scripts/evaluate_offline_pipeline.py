from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.rag import _secret_summary, _serialize_steps
from app.retrieval import DEFAULT_RANK_LIMIT, SHORT_PROCESS_ALL_STEPS_MAX, Step, rank_steps

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "tests/eval/synthetic_cases.json"
DEFAULT_BASELINE = REPO_ROOT / "tests/eval/offline_pipeline_baseline.json"

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


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("synthetic pipeline dataset must be a JSON array")
    return payload


def _milestone_text(label: str) -> str:
    try:
        return _MILESTONE_TEXT[label]
    except KeyError as exc:
        raise ValueError(f"unsupported synthetic milestone label: {label}") from exc


def _synthetic_steps(case: dict[str, Any]) -> tuple[list[Step], set[int], set[int]]:
    count = int(case["step_count"])
    if count <= 0:
        return [], set(), set()

    expected = case.get("expected_milestones", [])
    if not isinstance(expected, list) or len(expected) != 1 or not isinstance(expected[0], str):
        raise ValueError(f"{case['case_id']}: pipeline probe expects exactly one milestone label")

    target = max(1, min(count, count // 2 or 1))
    milestone = _milestone_text(expected[0])
    steps: list[Step] = []
    for number in range(1, count + 1):
        text = milestone if number == target else f"Movimento ordinário sintético {number}"
        steps.append(
            Step(
                id=uuid5(NAMESPACE_URL, f"rpy:{case['case_id']}:{number}"),
                step_number=number,
                text=text,
                title=None,
                source_step_number=number,
            )
        )

    if count <= SHORT_PROCESS_ALL_STEPS_MAX:
        policy_relevant = set(range(1, count + 1))
    else:
        policy_relevant = {1, target}
        policy_relevant.update(range(max(1, count - 4), count + 1))
    milestone_numbers = {target}
    return steps, policy_relevant, milestone_numbers


def _secret_context(case: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    marker = f"RESTRICTED-{case['case_id']}"
    sensitive = {
        marker,
        f"PARTY-{case['case_id']}",
        f"SUBJECT-{case['case_id']}",
        "987654.32",
    }
    return (
        {
            "code": marker,
            "class_name": "Procedimento Sigiloso Sintético",
            "secrecy_level": int(case["secrecy_level"]),
            "header": {
                "name": marker,
                "instance": int(case["instance"]),
                "area": "Área sintética permitida",
                "county": "Comarca sintética permitida",
                "amount": 987654.32,
            },
            "validation_parties": [{"name": f"PARTY-{case['case_id']}"}],
            "parties": [],
            "subjects": [{"name": f"SUBJECT-{case['case_id']}"}],
            "steps": [{"text": marker}],
        },
        sensitive,
    )


def evaluate_pipeline(cases: list[dict[str, Any]]) -> dict[str, Any]:
    milestone_expected = 0
    milestone_recovered = 0
    selected_total = 0
    relevant_selected = 0
    sourced_selected = 0
    secret_checks = 0
    secret_leaks = 0
    per_case: dict[str, dict[str, Any]] = {}

    for case in cases:
        case_id = str(case["case_id"])
        if int(case.get("secrecy_level", 0)) > 0:
            context, forbidden = _secret_context(case)
            summary = _secret_summary(context)
            leaked = sorted(value for value in forbidden if value in summary)
            secret_checks += len(forbidden)
            secret_leaks += len(leaked)
            per_case[case_id] = {
                "mode": "secret-local",
                "secret_leaks": leaked,
                "secret_checks": len(forbidden),
            }
            continue

        steps, policy_relevant, milestone_numbers = _synthetic_steps(case)
        if not steps:
            per_case[case_id] = {
                "mode": "retrieval",
                "selected": 0,
                "policy_relevant_selected": 0,
                "milestone_expected": 0,
                "milestone_recovered": 0,
                "source_presence": 1.0,
            }
            continue

        expected_label = str(case["expected_milestones"][0])
        ranked = rank_steps(
            query=_milestone_text(expected_label),
            steps=steps,
            vector_scores=None,
            limit=DEFAULT_RANK_LIMIT,
        )
        serialized = _serialize_steps(ranked)
        selected_numbers = {item.step.step_number for item in ranked}
        selected = len(ranked)
        relevant = len(selected_numbers & policy_relevant)
        recovered = len(selected_numbers & milestone_numbers)
        sourced = sum(
            1
            for item in serialized
            if isinstance(item.get("step_number"), int)
            and isinstance(item.get("text"), str)
            and bool(item["text"])
        )

        milestone_expected += len(milestone_numbers)
        milestone_recovered += recovered
        selected_total += selected
        relevant_selected += relevant
        sourced_selected += sourced
        per_case[case_id] = {
            "mode": "retrieval",
            "selected": selected,
            "policy_relevant_selected": relevant,
            "milestone_expected": len(milestone_numbers),
            "milestone_recovered": recovered,
            "source_presence": sourced / selected if selected else 1.0,
        }

    metrics = {
        "mandatory_milestone_recall": (
            milestone_recovered / milestone_expected if milestone_expected else 1.0
        ),
        "policy_candidate_precision": (
            relevant_selected / selected_total if selected_total else 1.0
        ),
        "source_presence_rate": (
            sourced_selected / selected_total if selected_total else 1.0
        ),
        "secret_summary_leakage_rate": (
            secret_leaks / secret_checks if secret_checks else 0.0
        ),
    }
    return {
        "cases": len(cases),
        "metrics": metrics,
        "counts": {
            "milestone_expected": milestone_expected,
            "milestone_recovered": milestone_recovered,
            "selected_candidates": selected_total,
            "policy_relevant_selected": relevant_selected,
            "sourced_selected": sourced_selected,
            "secret_checks": secret_checks,
            "secret_leaks": secret_leaks,
        },
        "case_metrics": per_case,
    }


def load_baseline(path: Path = DEFAULT_BASELINE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("offline pipeline baseline must be a JSON object")
    return payload


def compare_to_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for metric, contract in baseline["metrics"].items():
        direction = contract["direction"]
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
    parser = argparse.ArgumentParser(description="Probe provider-free Rpy retrieval and secrecy boundaries")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--check-baseline", action="store_true")
    args = parser.parse_args()

    report = evaluate_pipeline(load_dataset(args.dataset))
    failures: list[dict[str, Any]] = []
    if args.check_baseline:
        baseline = load_baseline(args.baseline)
        failures = compare_to_baseline(report, baseline)
        report["baseline_check"] = {
            "baseline_version": baseline.get("baseline_version"),
            "passed": not failures,
            "failures": failures,
        }

    # Build the CLI report from an explicit allowlist.  The evaluator keeps
    # source-derived values in memory for assertions, but its stdout is an
    # operational log boundary and must contain only aggregate evidence.
    metrics = report.get("metrics")
    counts = report.get("counts")
    safe_report: dict[str, Any] = {
        "cases": int(report.get("cases", 0)),
        "metrics": {
            name: float(metrics[name])
            for name in (
                "mandatory_milestone_recall",
                "policy_candidate_precision",
                "source_presence_rate",
                "secret_summary_leakage_rate",
            )
            if isinstance(metrics, dict) and name in metrics
        },
        "counts": {
            name: int(counts[name])
            for name in (
                "milestone_expected",
                "milestone_recovered",
                "selected_candidates",
                "policy_relevant_selected",
                "sourced_selected",
                "secret_checks",
                "secret_leaks",
            )
            if isinstance(counts, dict) and name in counts
        },
    }
    if "baseline_check" in report:
        baseline_check = report["baseline_check"]
        if isinstance(baseline_check, dict):
            safe_report["baseline_check"] = {
                "baseline_version": baseline_check.get("baseline_version"),
                "passed": bool(baseline_check.get("passed")),
                "failure_count": len(baseline_check.get("failures", [])),
            }

    print(json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
