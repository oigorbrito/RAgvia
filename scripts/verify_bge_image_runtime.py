from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.artifact_provenance import artifact_manifest, sha256_file
from app.bge_runtime_contract import EXPECTED_FLAGEMBEDDING_VERSION, validate_flagembedding_runtime
from app.json_utils import loads_strict_json

REQUIRED_OFFLINE_ENV = (
    "PIP_NO_INDEX",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "HF_DATASETS_OFFLINE",
)


def validate(*, model_dir: Path, environ: dict[str, str] | None = None) -> list[str]:
    env = dict(os.environ if environ is None else environ)
    errors: list[str] = []

    errors.extend(validate_flagembedding_runtime())

    if not model_dir.is_dir():
        errors.append(f"BGE model directory does not exist: {model_dir}")
    else:
        config = model_dir / "config.json"
        if not config.is_file():
            errors.append(f"BGE model artifact is missing config.json: {model_dir}")
        else:
            try:
                payload = loads_strict_json(config.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"BGE model config.json is invalid: {exc}")
            else:
                if not isinstance(payload, dict):
                    errors.append("BGE model config.json must contain an object")
                else:
                    hidden_size = payload.get("hidden_size")
                    if hidden_size not in {None, 1024}:
                        errors.append(
                            f"BGE model hidden_size must be 1024 when declared, got {hidden_size!r}"
                        )

    for key in REQUIRED_OFFLINE_ENV:
        value = str(env.get(key) or "").strip().casefold()
        if value not in {"1", "true", "yes", "on"}:
            errors.append(f"{key} must force offline mode in the BGE image")

    configured_path = str(env.get("BGE_EMBEDDING_PATH") or "").strip()
    if configured_path != str(model_dir):
        errors.append(
            f"BGE_EMBEDDING_PATH must equal the verified model directory {str(model_dir)!r}"
        )

    return errors


def evidence_report(
    *,
    model_dir: Path,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    errors = validate(model_dir=model_dir, environ=environ)
    if errors:
        raise RuntimeError("BGE embedding artifact is not deployment-ready")

    config = model_dir / "config.json"
    return {
        "report_version": 1,
        "model": "BAAI/bge-m3",
        "flagembedding_version": EXPECTED_FLAGEMBEDDING_VERSION,
        "artifact_config_sha256": sha256_file(config),
        "artifact_manifest": artifact_manifest(
            model_dir,
            label="BGE embedding artifact",
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify offline BGE image readiness")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--evidence-json",
        action="store_true",
        help="Emit machine-readable local-artifact evidence after validation",
    )
    args = parser.parse_args()
    errors = validate(model_dir=args.model_dir)
    if errors:
        for error in errors:
            print(f"bge image contract: {error}")
        return 1
    if args.evidence_json:
        print(
            json.dumps(
                evidence_report(model_dir=args.model_dir),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("bge image contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
