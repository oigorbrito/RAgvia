from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "tests/eval/synthetic_cases.json"
DEFAULT_BASELINE = REPO_ROOT / "tests/eval/baseline.json"


def _as_set(case: dict[str, Any], key: str) -> set[str]:
    value = case.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{case.get('case_id', '<unknown>')}: {key} must be a list of strings")
    return set(value)


def _case_metrics(case: dict[str, Any]) -> dict[str, float]:
    claims = case.get("claims", [])
    if not isinstance(claims, list):
        raise ValueError(f"{case.get('case_id', '<unknown>')}: claims must be a list")
    unsupported = 0
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
            raise ValueError(f"{case.get('case_id', '<unknown>')}: every claim must contain text")
        supported = claim.get("supported")
        if not isinstance(supported, bool):
            raise ValueError(
                f"{case.get('case_id', '<unknown>')}: every claim must contain boolean supported"
            )
        unsupported += int(not supported)

    expected_milestones = _as_set(case, "expected_milestones")
    predicted_milestones = _as_set(case, "predicted_milestones")
    expected_inconsistencies = _as_set(case, "expected_inconsistencies")
    predicted_inconsistencies = _as_set(case, "predicted_inconsistencies")
    expected_attention = _as_set(case, "expected_attention")
    predicted_attention = _as_set(case, "predicted_attention")

    return {
        "unsupported_assertion_rate": unsupported / len(claims) if claims else 0.0,
        "milestone_recall": (
            len(expected_milestones & predicted_milestones) / len(expected_milestones)
            if expected_milestones
            else 1.0
        ),
        "inconsistency_detection_recall": (
            len(expected_inconsistencies & predicted_inconsistencies)
            / len(expected_inconsistencies)
            if expected_inconsistencies
            else 1.0
        ),
        "attention_false_positive_rate": (
            len(predicted_attention - expected_attention) / len(predicted_attention)
            if predicted_attention
            else 0.0
        ),
    }


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        raise ValueError("synthetic evaluation dataset must not be empty")

    claim_total = 0
    unsupported_claims = 0
    expected_milestones = 0
    recovered_milestones = 0
    expected_inconsistencies = 0
    detected_inconsistencies = 0
    predicted_attention = 0
    false_positive_attention = 0
    domains: set[str] = set()
    special_cases: set[str] = set()
    per_case: dict[str, dict[str, float]] = {}

    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every case must have a non-empty case_id")
        if case_id in per_case:
            raise ValueError(f"duplicate case_id: {case_id}")

        domain = case.get("domain")
        if not isinstance(domain, str) or not domain:
            raise ValueError(f"{case_id}: domain must be a non-empty string")
        domains.add(domain)

        tags = _as_set(case, "tags")
        special_cases.update(tags)

        claims = case.get("claims", [])
        metrics = _case_metrics(case)
        per_case[case_id] = metrics
        claim_total += len(claims)
        unsupported_claims += sum(int(not bool(claim["supported"])) for claim in claims)

        expected = _as_set(case, "expected_milestones")
        predicted = _as_set(case, "predicted_milestones")
        expected_milestones += len(expected)
        recovered_milestones += len(expected & predicted)

        expected_issues = _as_set(case, "expected_inconsistencies")
        predicted_issues = _as_set(case, "predicted_inconsistencies")
        expected_inconsistencies += len(expected_issues)
        detected_inconsistencies += len(expected_issues & predicted_issues)

        expected_attention = _as_set(case, "expected_attention")
        predicted_attention_items = _as_set(case, "predicted_attention")
        predicted_attention += len(predicted_attention_items)
        false_positive_attention += len(predicted_attention_items - expected_attention)

    return {
        "cases": len(cases),
        "coverage": {
            "domains": sorted(domains),
            "tags": sorted(special_cases),
        },
        "metrics": {
            "unsupported_assertion_rate": (
                unsupported_claims / claim_total if claim_total else 0.0
            ),
            "milestone_recall": (
                recovered_milestones / expected_milestones if expected_milestones else 1.0
            ),
            "inconsistency_detection_recall": (
                detected_inconsistencies / expected_inconsistencies
                if expected_inconsistencies
                else 1.0
            ),
            "attention_false_positive_rate": (
                false_positive_attention / predicted_attention if predicted_attention else 0.0
            ),
        },
        "case_metrics": per_case,
        "counts": {
            "claims": claim_total,
            "unsupported_claims": unsupported_claims,
            "expected_milestones": expected_milestones,
            "recovered_milestones": recovered_milestones,
            "expected_inconsistencies": expected_inconsistencies,
            "detected_inconsistencies": detected_inconsistencies,
            "predicted_attention": predicted_attention,
            "false_positive_attention": false_positive_attention,
        },
    }


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("synthetic evaluation dataset must be a JSON array")
    return payload


def load_baseline(path: Path = DEFAULT_BASELINE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("synthetic evaluation baseline must be a JSON object")
    return payload


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def runtime_metadata() -> dict[str, Any]:
    from app.rag import MODEL, PROMPT_VERSION
    from app.retrieval import (
        DEFAULT_RANK_LIMIT,
        LEXICAL_WEIGHT,
        MANDATORY_RECENT_STEPS,
        RECENCY_BOOST_MAX,
        SHORT_PROCESS_ALL_STEPS_MAX,
        VECTOR_WEIGHT,
    )

    return {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "retrieval": {
            "short_process_all_steps_max": SHORT_PROCESS_ALL_STEPS_MAX,
            "default_rank_limit": DEFAULT_RANK_LIMIT,
            "lexical_source": "literal_bm25",
            "lexical_weight": LEXICAL_WEIGHT,
            "vector_weight": VECTOR_WEIGHT,
            "postgresql_fts_role": "candidate_discovery_and_index_verification_only",
            "vector_optional_when_unconfigured": True,
            "recency_boost_max": RECENCY_BOOST_MAX,
            "mandatory_recent_steps": MANDATORY_RECENT_STEPS,
        },
    }


def _violates(actual: float, threshold: dict[str, Any]) -> bool:
    direction = threshold.get("direction")
    value = threshold.get("value")
    if direction not in {"min", "max"} or not isinstance(value, int | float):
        raise ValueError(f"invalid threshold: {threshold!r}")
    if direction == "min":
        return actual < float(value)
    return actual > float(value)


def compare_to_baseline(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    dataset_path: Path = DEFAULT_DATASET,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    dataset_meta = baseline.get("dataset", {})
    expected_blob = dataset_meta.get("git_blob_sha")
    actual_blob = _git_blob_sha(dataset_path)
    if expected_blob != actual_blob:
        failures.append(
            {
                "scope": "dataset",
                "metric": "git_blob_sha",
                "expected": expected_blob,
                "actual": actual_blob,
                "message": "dataset changed without an explicit baseline update",
            }
        )

    expected_runtime = baseline.get("runtime")
    actual_runtime = runtime_metadata()
    if expected_runtime != actual_runtime:
        failures.append(
            {
                "scope": "runtime",
                "metric": "configuration",
                "expected": expected_runtime,
                "actual": actual_runtime,
                "message": "model, prompt or retrieval configuration changed without a baseline update",
            }
        )

    aggregate_thresholds = baseline.get("aggregate_thresholds", {})
    for metric, threshold in aggregate_thresholds.items():
        actual = float(report["metrics"][metric])
        if _violates(actual, threshold):
            failures.append(
                {
                    "scope": "aggregate",
                    "metric": metric,
                    "threshold": threshold,
                    "actual": actual,
                    "message": f"aggregate regression in {metric}",
                }
            )

    case_thresholds = baseline.get("case_thresholds", {})
    defaults = case_thresholds.get("defaults", {})
    overrides = case_thresholds.get("overrides", {})
    case_metrics = report.get("case_metrics", {})
    unknown_overrides = sorted(set(overrides) - set(case_metrics))
    if unknown_overrides:
        failures.append(
            {
                "scope": "baseline",
                "metric": "case_overrides",
                "actual": unknown_overrides,
                "message": "baseline contains overrides for missing cases",
            }
        )

    for case_id, metrics in sorted(case_metrics.items()):
        thresholds = dict(defaults)
        thresholds.update(overrides.get(case_id, {}))
        for metric, threshold in thresholds.items():
            actual = float(metrics[metric])
            if _violates(actual, threshold):
                failures.append(
                    {
                        "scope": "case",
                        "case_id": case_id,
                        "metric": metric,
                        "threshold": threshold,
                        "actual": actual,
                        "message": f"case regression in {case_id}: {metric}",
                    }
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the provider-free synthetic RAG dataset")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--check-baseline", action="store_true")
    args = parser.parse_args()

    report = evaluate(load_dataset(args.dataset))
    failures: list[dict[str, Any]] = []
    if args.check_baseline:
        baseline = load_baseline(args.baseline)
        failures = compare_to_baseline(report, baseline, dataset_path=args.dataset)
        report["baseline_check"] = {
            "baseline_version": baseline.get("baseline_version"),
            "dataset_version": baseline.get("dataset", {}).get("version"),
            "passed": not failures,
            "failures": failures,
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
