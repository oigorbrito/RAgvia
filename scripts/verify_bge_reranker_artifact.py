from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.bge_runtime_contract import validate_flagembedding_runtime
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
        errors.append(f"BGE reranker directory does not exist: {model_dir}")
    else:
        config = model_dir / "config.json"
        if not config.is_file():
            errors.append(f"BGE reranker artifact is missing config.json: {model_dir}")
        else:
            try:
                payload = loads_strict_json(config.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"BGE reranker config.json is invalid: {exc}")
            else:
                if not isinstance(payload, dict):
                    errors.append("BGE reranker config.json must contain an object")

    for key in REQUIRED_OFFLINE_ENV:
        value = str(env.get(key) or "").strip().casefold()
        if value not in {"1", "true", "yes", "on"}:
            errors.append(f"{key} must force offline mode for the BGE reranker")

    configured_path = str(env.get("BGE_RERANKER_PATH") or "").strip()
    if configured_path != str(model_dir):
        errors.append(
            f"BGE_RERANKER_PATH must equal the verified reranker directory {str(model_dir)!r}"
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify offline BGE reranker artifact readiness")
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(model_dir=args.model_dir)
    if errors:
        for error in errors:
            print(f"bge reranker contract: {error}")
        return 1
    print("bge reranker contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
