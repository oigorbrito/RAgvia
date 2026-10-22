from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app import bge_runtime_contract

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_bge_image_runtime.py"
spec = importlib.util.spec_from_file_location("verify_bge_image_runtime", MODULE_PATH)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def _offline_env(model_dir: Path) -> dict[str, str]:
    return {
        "PIP_NO_INDEX": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "BGE_EMBEDDING_PATH": str(model_dir),
    }


def test_bge_image_readiness_accepts_local_1024_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{"hidden_size": 1024}', encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    assert verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir)) == []


def test_bge_image_readiness_rejects_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{"hidden_size": 1024}', encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))
    assert "FlagEmbedding is not installed" in errors


def test_bge_image_readiness_rejects_online_or_wrong_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{"hidden_size": 768}', encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")
    env = _offline_env(model_dir)
    env["HF_HUB_OFFLINE"] = "0"
    env["BGE_EMBEDDING_PATH"] = "/other/model"

    errors = verifier.validate(model_dir=model_dir, environ=env)
    assert any("hidden_size must be 1024" in error for error in errors)
    assert "HF_HUB_OFFLINE must force offline mode in the BGE image" in errors
    assert any("BGE_EMBEDDING_PATH must equal" in error for error in errors)


def test_bge_dockerfile_uses_hashed_external_offline_contexts() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile.bge").read_text(
        encoding="utf-8"
    )
    assert "COPY --from=bge_wheels" in dockerfile
    assert "COPY --from=bge_model" in dockerfile
    assert "COPY scripts/verify_bge_image_runtime.py" in dockerfile
    assert "requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--no-index" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert "USER 10001" in dockerfile


@pytest.mark.parametrize(
    "raw",
    [
        '{"hidden_size":1024,"hidden_size":768}',
        '{"hidden_size":NaN}',
    ],
)
def test_bge_image_readiness_rejects_ambiguous_config_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw: str,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))
    if not any("config.json is invalid" in error for error in errors):
        raise AssertionError(f"expected invalid config error, got {errors!r}")


@pytest.mark.parametrize("raw", ["[]", '"model"'])
def test_bge_image_readiness_rejects_non_object_config_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw: str,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))
    if "BGE model config.json must contain an object" not in errors:
        raise AssertionError(f"expected object-root error, got {errors!r}")


def test_bge_embedding_evidence_report_binds_full_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    config = model_dir / "config.json"
    config.write_text('{"hidden_size": 1024}', encoding="utf-8")
    weights = model_dir / "model.safetensors"
    weights.write_bytes(b"synthetic-bge-weights")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    report = verifier.evidence_report(
        model_dir=model_dir,
        environ=_offline_env(model_dir),
    )

    assert report["report_version"] == 1
    assert report["model"] == "BAAI/bge-m3"
    assert report["flagembedding_version"] == "1.4.2"
    assert len(report["artifact_config_sha256"]) == 64
    manifest = report["artifact_manifest"]
    assert manifest["file_count"] == 2
    assert manifest["total_bytes"] == config.stat().st_size + weights.stat().st_size
    assert len(manifest["sha256"]) == 64

    original = manifest["sha256"]
    weights.write_bytes(b"changed-synthetic-bge-weights")
    changed = verifier.evidence_report(
        model_dir=model_dir,
        environ=_offline_env(model_dir),
    )
    assert changed["artifact_manifest"]["sha256"] != original


def test_bge_embedding_evidence_report_fails_closed_when_contract_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{"hidden_size": 768}', encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    with pytest.raises(RuntimeError, match="not deployment-ready"):
        verifier.evidence_report(
            model_dir=model_dir,
            environ=_offline_env(model_dir),
        )
