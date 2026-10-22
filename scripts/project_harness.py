from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
IMAGE_WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"


@dataclass(frozen=True, slots=True)
class Observation:
    name: str
    kind: str
    status: str
    evidence: str
    detail: str


def _run_guardrail(name: str, relative_script: str) -> Observation:
    command = [sys.executable, str(ROOT / relative_script)]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part.strip()
    )
    return Observation(
        name=name,
        kind="guardrail",
        status="pass" if completed.returncode == 0 else "fail",
        evidence=" ".join(command),
        detail=output or f"exit={completed.returncode}",
    )


def _ci_gate_observations() -> list[Observation]:
    if not CI.is_file():
        return [
            Observation(
                name="ci-workflow",
                kind="evidence-wiring",
                status="fail",
                evidence=str(CI.relative_to(ROOT)),
                detail="canonical CI workflow is missing",
            )
        ]

    text = CI.read_text(encoding="utf-8")
    expected = {
        "static-quality": "ruff check app scripts tests",
        "unit-tests": "pytest -q tests --ignore=tests/integration",
        "hypothesis-reproducibility": "--hypothesis-seed=20260918",
        "synthetic-rag-eval": "python scripts/evaluate_synthetic_rag.py --check-baseline",
        "offline-pipeline-eval": "python scripts/evaluate_offline_pipeline.py --check-baseline",
        "offline-generation-eval": "python scripts/evaluate_offline_generation.py --check-baseline",
        "frontend-behavior": "node tests/frontend_behavior_test.mjs",
        "container-runtime-smoke": "sh scripts/verify_image_runtime.sh",
        "backup-restore-drill": "sh scripts/verify_backup_restore.sh",
        "postgres-integration": "pytest -q tests/integration",
        "offline-release-smoke": "./scripts/smoke_offline.sh",
    }

    observations: list[Observation] = []
    for name, command_fragment in expected.items():
        present = command_fragment in text
        observations.append(
            Observation(
                name=name,
                kind="evidence-wiring",
                status="pass" if present else "fail",
                evidence=str(CI.relative_to(ROOT)),
                detail=(
                    f"wired command contains: {command_fragment}"
                    if present
                    else f"missing canonical evidence command: {command_fragment}"
                ),
            )
        )
    return observations



def _release_workflow_observations() -> list[Observation]:
    if not IMAGE_WORKFLOW.is_file():
        return [
            Observation(
                name="image-workflow",
                kind="evidence-wiring",
                status="fail",
                evidence=str(IMAGE_WORKFLOW.relative_to(ROOT)),
                detail="image release workflow is missing",
            )
        ]

    text = IMAGE_WORKFLOW.read_text(encoding="utf-8")
    expected = {
        "image-project-harness": "python scripts/project_harness.py",
        "image-unit-tests": "pytest -q tests --ignore=tests/integration",
        "image-postgres-integration": "pytest -q tests/integration",
        "published-image-smoke": "sh scripts/verify_image_runtime.sh",
        "published-image-attestation": "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
        "published-image-attestation-digest": "subject-digest: ${{ steps.build.outputs.digest }}",
        "attestation-id-token-permission": "id-token: write",
        "attestation-write-permission": "attestations: write",
    }
    observations: list[Observation] = []
    for name, command_fragment in expected.items():
        present = command_fragment in text
        observations.append(
            Observation(
                name=name,
                kind="evidence-wiring",
                status="pass" if present else "fail",
                evidence=str(IMAGE_WORKFLOW.relative_to(ROOT)),
                detail=(
                    f"wired command contains: {command_fragment}"
                    if present
                    else f"missing release evidence command: {command_fragment}"
                ),
            )
        )
    return observations


def _documentation_observations() -> list[Observation]:
    required = (
        "AGENTS.md",
        "docs/engineering/empirical-engineering.md",
        "docs/engineering/adversarial-fuzzing.md",
        "docs/engineering/project-harness.md",
        "CONTRIBUTING.md",
    )
    observations: list[Observation] = []
    for relative in required:
        path = ROOT / relative
        observations.append(
            Observation(
                name=f"documentation:{relative}",
                kind="evidence-contract",
                status="pass" if path.is_file() else "fail",
                evidence=relative,
                detail="present" if path.is_file() else "missing",
            )
        )
    return observations


def collect_observations() -> list[Observation]:
    observations = [
        _run_guardrail("migration-harness", "scripts/migration_harness.py"),
        _run_guardrail("release-harness", "scripts/release_harness.py"),
    ]
    observations.extend(_ci_gate_observations())
    observations.extend(_release_workflow_observations())
    observations.extend(_documentation_observations())
    return observations


def _print_text(observations: list[Observation]) -> None:
    failed = [item for item in observations if item.status != "pass"]
    print("Project harness evidence:")
    for item in observations:
        print(
            f" - {item.status.upper():4} [{item.kind}] {item.name}: "
            f"{item.detail}"
        )
    if failed:
        print(f"Project harness: FAILED ({len(failed)} failed observations)")
    else:
        print(
            "Project harness: OK "
            f"({len(observations)} reproducible guardrail/wiring observations)"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify cheap project invariants and that stronger behavioral/operational "
            "evidence remains wired into canonical CI."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the evidence observations as JSON",
    )
    args = parser.parse_args()

    observations = collect_observations()
    if args.json:
        print(json.dumps([asdict(item) for item in observations], indent=2))
    else:
        _print_text(observations)

    return 1 if any(item.status != "pass" for item in observations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
