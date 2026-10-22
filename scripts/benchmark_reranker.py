from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from app.artifact_provenance import artifact_manifest, sha256_file
from app.bge_runtime_contract import (
    EXPECTED_FLAGEMBEDDING_VERSION,
    validate_flagembedding_runtime,
)
from app.reranker_bge import BGERerankerScorer, DEFAULT_BGE_RERANKER_MODEL
from app.reranking import select_context_steps
from app.retrieval import Step

_EVALUATOR_PATH = Path(__file__).with_name("evaluate_offline_pipeline.py")
_SPEC = importlib.util.spec_from_file_location("evaluate_offline_pipeline_for_reranker", _EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EVALUATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVALUATOR)
DEFAULT_DATASET = _EVALUATOR.DEFAULT_DATASET
ROOT = Path(__file__).resolve().parents[1]


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return f"<external>/{resolved.name}"


def _host_provenance() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
    }


def _require_bge_runtime() -> None:
    runtime_errors = validate_flagembedding_runtime()
    if runtime_errors:
        raise RuntimeError(
            "BGE runtime contract failed: " + "; ".join(runtime_errors)
        )


def _runtime_provenance(scorer_name: str, scorer: Any) -> dict[str, Any]:
    if scorer_name == "synthetic":
        return {"kind": "synthetic"}

    if scorer_name != "bge":
        raise ValueError(f"unsupported scorer: {scorer_name}")

    _require_bge_runtime()

    model = str(getattr(scorer, "model", "")).strip()
    if model != DEFAULT_BGE_RERANKER_MODEL:
        raise RuntimeError(
            "BGE benchmark evidence requires model "
            f"{DEFAULT_BGE_RERANKER_MODEL}, got {model!r}"
        )

    artifact_path = getattr(scorer, "artifact_path", None)
    config_sha256: str | None = None
    artifact_manifest_data: dict[str, Any] | None = None
    if artifact_path is not None:
        artifact_root = Path(str(artifact_path))
        config_path = artifact_root / "config.json"
        if not config_path.is_file():
            raise RuntimeError(
                f"BGE reranker artifact is missing config.json: {artifact_path}"
            )
        config_sha256 = sha256_file(config_path)
        print(
            f"benchmark provenance: hashing BGE artifact {artifact_root}",
            file=sys.stderr,
        )
        artifact_manifest_data = artifact_manifest(
            artifact_root,
            label="BGE reranker artifact",
        )

    return {
        "kind": "bge",
        "model": model,
        "artifact_path": (
            _display_path(Path(str(artifact_path)))
            if artifact_path is not None
            else None
        ),
        "local_artifact_bound": artifact_path is not None,
        "artifact_config_sha256": config_sha256,
        "artifact_manifest": artifact_manifest_data,
        "use_fp16": bool(getattr(scorer, "use_fp16", False)),
        "flagembedding_version": EXPECTED_FLAGEMBEDDING_VERSION,
    }


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    return _EVALUATOR.load_dataset(path)


def _synthetic_steps(case: dict[str, Any]):
    return _EVALUATOR._synthetic_steps(case)


def _milestone_text(label: str) -> str:
    return _EVALUATOR._milestone_text(label)


def _synthetic_scorer(query: str, steps: list[Step]) -> dict[Any, float]:
    terms = {term.casefold() for term in query.split() if term.strip()}
    scores: dict[Any, float] = {}
    for step in steps:
        haystack = step.searchable_text.casefold()
        scores[step.id] = float(sum(1 for term in terms if term in haystack))
    return scores


async def _score_synthetic(query: str, steps: list[Step]) -> dict[Any, float]:
    return _synthetic_scorer(query, steps)


async def benchmark_cases(
    cases: list[dict[str, Any]],
    *,
    scorer_name: str = "synthetic",
    scorer: Any | None = None,
) -> dict[str, Any]:
    if scorer_name not in {"synthetic", "bge"}:
        raise ValueError(f"unsupported scorer: {scorer_name}")
    if scorer is None:
        scorer = _score_synthetic if scorer_name == "synthetic" else BGERerankerScorer()

    baseline_selected = 0
    reranked_selected = 0
    baseline_relevant = 0
    reranked_relevant = 0
    baseline_milestones = 0
    reranked_milestones = 0
    milestone_expected = 0
    measured_cases = 0
    baseline_ms = 0.0
    reranked_ms = 0.0

    for case in cases:
        if int(case.get("secrecy_level", 0)) > 0:
            continue
        steps, policy_relevant, milestone_numbers = _synthetic_steps(case)
        if len(steps) <= 40:
            continue
        query = _milestone_text(str(case["expected_milestones"][0]))

        started = perf_counter()
        baseline = await select_context_steps(query=query, steps=steps)
        baseline_ms += (perf_counter() - started) * 1000

        started = perf_counter()
        reranked = await select_context_steps(query=query, steps=steps, scorer=scorer)
        reranked_ms += (perf_counter() - started) * 1000

        baseline_numbers = {item.step.step_number for item in baseline}
        reranked_numbers = {item.step.step_number for item in reranked}
        baseline_selected += len(baseline_numbers)
        reranked_selected += len(reranked_numbers)
        baseline_relevant += len(baseline_numbers & policy_relevant)
        reranked_relevant += len(reranked_numbers & policy_relevant)
        baseline_milestones += len(baseline_numbers & milestone_numbers)
        reranked_milestones += len(reranked_numbers & milestone_numbers)
        milestone_expected += len(milestone_numbers)
        measured_cases += 1

    def precision(relevant: int, selected: int) -> float:
        return relevant / selected if selected else 1.0

    def recall(recovered: int) -> float:
        return recovered / milestone_expected if milestone_expected else 1.0

    return {
        "scorer": scorer_name,
        "measured_long_cases": measured_cases,
        "baseline": {
            "mandatory_milestone_recall": recall(baseline_milestones),
            "policy_candidate_precision": precision(baseline_relevant, baseline_selected),
            "selected_candidates": baseline_selected,
            "elapsed_ms": round(baseline_ms, 3),
        },
        "reranked": {
            "mandatory_milestone_recall": recall(reranked_milestones),
            "policy_candidate_precision": precision(reranked_relevant, reranked_selected),
            "selected_candidates": reranked_selected,
            "elapsed_ms": round(reranked_ms, 3),
        },
        "external_provider_cost_usd": 0.0,
        "hardware_cost_usd": None,
    }



async def benchmark_report(
    dataset_path: Path = DEFAULT_DATASET,
    *,
    scorer_name: str = "synthetic",
) -> dict[str, Any]:
    if scorer_name == "synthetic":
        scorer: Any = _score_synthetic
    elif scorer_name == "bge":
        _require_bge_runtime()
        scorer = BGERerankerScorer()
    else:
        raise ValueError(f"unsupported scorer: {scorer_name}")

    dataset_provenance = {
        "path": _display_path(dataset_path),
        "sha256": sha256_file(dataset_path),
    }
    runtime_provenance = _runtime_provenance(scorer_name, scorer)
    host_provenance = _host_provenance()

    report = await benchmark_cases(
        load_dataset(dataset_path),
        scorer_name=scorer_name,
        scorer=scorer,
    )
    return {
        "report_version": 2,
        "dataset": dataset_provenance,
        "runtime": runtime_provenance,
        "host": host_provenance,
        **report,
    }


def require_deployment_evidence(report: dict[str, Any]) -> None:
    runtime = report.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("kind") != "bge":
        raise RuntimeError("deployment evidence requires --scorer bge")
    if runtime.get("local_artifact_bound") is not True:
        raise RuntimeError(
            "deployment evidence requires BGE_RERANKER_PATH bound to a verified local artifact"
        )
    manifest = runtime.get("artifact_manifest")
    if not isinstance(manifest, dict) or not manifest.get("sha256"):
        raise RuntimeError("deployment evidence requires a complete local artifact manifest")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Rpy retrieval with and without reranking")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scorer", choices=("synthetic", "bge"), default="synthetic")
    parser.add_argument(
        "--deployment-evidence",
        action="store_true",
        help="Fail unless the report is a local-artifact-bound real BGE deployment run",
    )
    args = parser.parse_args()
    report = asyncio.run(
        benchmark_report(args.dataset, scorer_name=args.scorer)
    )
    if args.deployment_evidence:
        require_deployment_evidence(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
