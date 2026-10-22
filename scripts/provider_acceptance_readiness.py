from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env.production.example"
DEFAULT_LIVE_WORKFLOW = ROOT / ".github" / "workflows" / "adversarial-live.yml"
DEFAULT_PROVIDER_SMOKE_WORKFLOW = ROOT / ".github" / "workflows" / "provider-live-smoke.yml"
DEFAULT_RUNBOOK = ROOT / "docs" / "release" / "provider-acceptance.md"

CREDENTIAL_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "JUDIT_API_KEY",
    "JUDIT_WEBHOOK_TOKEN",
    "DATAJUD_API_KEY",
    "COHERE_API_KEY",
)
DISABLED_GATES = {
    "JUDIT_ATTACHMENTS_ENABLED": "false",
    "DATAJUD_ENABLED": "false",
    "DATAJUD_AUTHORIZED_USE": "false",
    "ALLOW_EXTERNAL_EMBEDDINGS": "false",
    "RERANKER_ENABLED": "false",
    "ALLOW_EXTERNAL_RERANKER": "false",
}
RUNBOOK_MARKERS = (
    "## 1. Judit acquisition",
    "### Optional DataJud enrichment",
    "## 2. Anthropic generation",
    "### Cohere Embed v4",
    "### BGE reranker",
    "### Cohere Rerank",
)


def parse_env_template(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment template line: {raw_line!r}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _safe_template_credential(value: str) -> bool:
    return not value or value.startswith("replace-with-")


def readiness_report(
    *,
    env_path: Path = DEFAULT_ENV,
    live_workflow_path: Path = DEFAULT_LIVE_WORKFLOW,
    provider_smoke_workflow_path: Path = DEFAULT_PROVIDER_SMOKE_WORKFLOW,
    runbook_path: Path = DEFAULT_RUNBOOK,
) -> dict[str, Any]:
    errors: list[str] = []
    values = parse_env_template(env_path)

    for key in CREDENTIAL_KEYS:
        if key not in values:
            errors.append(f"production template missing credential field: {key}")
            continue
        if not _safe_template_credential(values[key]):
            errors.append(
                f"production template credential field must remain empty or placeholder-only: {key}"
            )

    for key, expected in DISABLED_GATES.items():
        actual = values.get(key)
        if actual != expected:
            errors.append(
                f"production template must keep {key}={expected} before online acceptance"
            )

    workflow = live_workflow_path.read_text(encoding="utf-8")
    for marker in (
        "workflow_dispatch:",
        "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}",
        'if [ -z "$ANTHROPIC_API_KEY" ]; then',
        "armed but not executed",
        "python scripts/evaluate_prompt_injection_live.py",
    ):
        if marker not in workflow:
            errors.append(f"live Anthropic workflow missing safety marker: {marker}")

    provider_smoke_workflow = provider_smoke_workflow_path.read_text(encoding="utf-8")
    for marker in (
        "workflow_dispatch:",
        "public_cnj:",
        "PROVIDER_ACCEPTANCE_CNJ: ${{ github.event.inputs.public_cnj || secrets.PROVIDER_ACCEPTANCE_CNJ }}",
        "python scripts/provider_live_smoke.py --preflight-cnj",
        "PROVIDER_ACCEPTANCE_AUTHORIZED: ${{ secrets.PROVIDER_ACCEPTANCE_AUTHORIZED }}",
        'JUDIT_ATTACHMENTS_ENABLED: "false"',
        "armed but not executed",
        "python scripts/provider_live_smoke.py",
        "--provider both",
        "--provider datajud",
        "github.event.inputs.mode == 'datajud'",
        "--capture-file provider-acceptance-artifacts/provider-smoke-capture.json",
    ):
        if marker not in provider_smoke_workflow:
            errors.append(f"live provider smoke workflow missing safety marker: {marker}")

    runbook = runbook_path.read_text(encoding="utf-8")
    for marker in RUNBOOK_MARKERS:
        if marker not in runbook:
            errors.append(f"provider acceptance runbook missing boundary: {marker}")

    providers = {
        "judit": {
            "state": "ready_to_provision",
            "live_requirement": "JUDIT_API_KEY + JUDIT_WEBHOOK_TOKEN + authorized CNJ/tenant",
            "credentials_present_in_repository": False,
        },
        "anthropic": {
            "state": "ready_to_provision",
            "live_requirement": "ANTHROPIC_API_KEY + authorized non-secret process",
            "credentials_present_in_repository": False,
        },
        "datajud": {
            "state": "disabled_pending_authorization",
            "live_requirement": (
                "DATAJUD_AUTHORIZED_USE=true + DATAJUD_ENABLED=true + DATAJUD_API_KEY"
            ),
            "credentials_present_in_repository": False,
        },
        "cohere": {
            "state": "disabled_pending_authorization",
            "live_requirement": (
                "separate external embedding/reranker authorization + COHERE_API_KEY"
            ),
            "credentials_present_in_repository": False,
        },
        "bge": {
            "state": "artifact_evidence_pending",
            "live_requirement": "real local artifacts + verifier/benchmark/reindex on target hardware",
            "credentials_present_in_repository": False,
        },
    }

    return {
        "passed": not errors,
        "errors": errors,
        "providers": providers,
        "network_calls_performed": False,
        "secrets_required_for_this_check": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that online provider acceptance is armed safely without provisioning "
            "credentials or performing network calls."
        )
    )
    parser.add_argument("--json", action="store_true", help="Emit the readiness matrix as JSON")
    args = parser.parse_args()

    report = readiness_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for provider, details in report["providers"].items():
            print(f"{provider}: {details['state']}")
        for error in report["errors"]:
            print(f"provider readiness: {error}")

    if report["passed"]:
        print("provider readiness: ok (credential-free; no network calls)")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
