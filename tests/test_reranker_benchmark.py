from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_reranker.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_reranker", _BENCHMARK_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BENCHMARK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK)


def test_synthetic_reranker_benchmark_preserves_mandatory_recall_and_reduces_context() -> None:
    report = asyncio.run(
        _BENCHMARK.benchmark_cases(_BENCHMARK.load_dataset(), scorer_name="synthetic")
    )

    assert report["measured_long_cases"] > 0
    assert report["baseline"]["mandatory_milestone_recall"] == 1.0
    assert report["reranked"]["mandatory_milestone_recall"] == 1.0
    assert report["reranked"]["selected_candidates"] <= report["baseline"]["selected_candidates"]
    assert report["reranked"]["policy_candidate_precision"] >= report["baseline"]["policy_candidate_precision"]
    assert report["external_provider_cost_usd"] == 0.0
    assert report["hardware_cost_usd"] is None
    assert report["baseline"]["elapsed_ms"] >= 0.0
    assert report["reranked"]["elapsed_ms"] >= 0.0


def test_reranker_benchmark_report_binds_dataset_and_host(tmp_path: Path) -> None:
    dataset = tmp_path / "synthetic_cases.json"
    dataset.write_bytes(_BENCHMARK.DEFAULT_DATASET.read_bytes())

    report = asyncio.run(
        _BENCHMARK.benchmark_report(dataset, scorer_name="synthetic")
    )

    expected_sha256 = hashlib.sha256(dataset.read_bytes()).hexdigest()
    if report["report_version"] != 2:
        raise AssertionError(f"unexpected report version: {report['report_version']!r}")
    if report["dataset"]["sha256"] != expected_sha256:
        raise AssertionError(f"unexpected dataset hash: {report['dataset']!r}")
    if report["runtime"] != {"kind": "synthetic"}:
        raise AssertionError(f"unexpected synthetic runtime: {report['runtime']!r}")
    if report["dataset"]["path"] != "<external>/synthetic_cases.json":
        raise AssertionError(f"unexpected redacted dataset path: {report['dataset']!r}")
    for key in ("python_version", "system", "release", "machine", "cpu_count"):
        if key not in report["host"]:
            raise AssertionError(f"host provenance missing {key!r}: {report['host']!r}")


def test_bge_runtime_provenance_binds_model_config_and_locked_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    config = model_dir / "config.json"
    config.write_text('{"model_type":"xlm-roberta"}', encoding="utf-8")
    weights = model_dir / "model.safetensors"
    weights.write_bytes(b"synthetic-weights")
    monkeypatch.setattr(_BENCHMARK, "validate_flagembedding_runtime", lambda: [])

    scorer = SimpleNamespace(
        model="BAAI/bge-reranker-v2-m3",
        artifact_path=str(model_dir),
        use_fp16=True,
    )
    provenance = _BENCHMARK._runtime_provenance("bge", scorer)

    if provenance["model"] != "BAAI/bge-reranker-v2-m3":
        raise AssertionError(f"unexpected model provenance: {provenance!r}")
    if provenance["artifact_path"] != "<external>/bge-reranker-v2-m3":
        raise AssertionError(f"unexpected artifact path: {provenance!r}")
    if provenance["local_artifact_bound"] is not True:
        raise AssertionError(f"expected local artifact binding: {provenance!r}")
    if provenance["artifact_config_sha256"] != hashlib.sha256(
        config.read_bytes()
    ).hexdigest():
        raise AssertionError(f"unexpected config hash: {provenance!r}")
    manifest = provenance["artifact_manifest"]
    if manifest["file_count"] != 2:
        raise AssertionError(f"unexpected artifact file count: {provenance!r}")
    if manifest["total_bytes"] != config.stat().st_size + weights.stat().st_size:
        raise AssertionError(f"unexpected artifact byte count: {provenance!r}")
    if len(manifest["sha256"]) != 64:
        raise AssertionError(f"unexpected artifact manifest hash: {provenance!r}")

    original_manifest_sha256 = manifest["sha256"]
    weights.write_bytes(b"changed-synthetic-weights")
    changed = _BENCHMARK._runtime_provenance("bge", scorer)
    if changed["artifact_manifest"]["sha256"] == original_manifest_sha256:
        raise AssertionError("artifact manifest hash must change with model bytes")

    if provenance["flagembedding_version"] != "1.4.2":
        raise AssertionError(f"unexpected FlagEmbedding version: {provenance!r}")
    if provenance["use_fp16"] is not True:
        raise AssertionError(f"unexpected FP16 provenance: {provenance!r}")


def test_bge_runtime_provenance_fails_closed_on_runtime_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _BENCHMARK,
        "validate_flagembedding_runtime",
        lambda: ["FlagEmbedding version mismatch"],
    )
    scorer = SimpleNamespace(
        model="BAAI/bge-reranker-v2-m3",
        artifact_path=None,
        use_fp16=False,
    )

    with pytest.raises(RuntimeError, match="BGE runtime contract failed"):
        _BENCHMARK._runtime_provenance("bge", scorer)


def test_benchmark_cases_rejects_mislabeled_injected_scorer() -> None:
    async def scorer(query, steps):
        return {}

    with pytest.raises(ValueError, match="unsupported scorer"):
        asyncio.run(
            _BENCHMARK.benchmark_cases(
                [],
                scorer_name="other",
                scorer=scorer,
            )
        )


def test_bge_benchmark_report_validates_runtime_before_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeScorer:
        model = "BAAI/bge-reranker-v2-m3"
        artifact_path = None
        use_fp16 = False

        def __init__(self) -> None:
            self.called = False

        async def __call__(self, query, steps):
            self.called = True
            return {step.id: 0.0 for step in steps}

    scorer = FakeScorer()
    constructed = False

    def construct_scorer():
        nonlocal constructed
        constructed = True
        return scorer

    monkeypatch.setattr(_BENCHMARK, "BGERerankerScorer", construct_scorer)
    monkeypatch.setattr(
        _BENCHMARK,
        "validate_flagembedding_runtime",
        lambda: ["FlagEmbedding version mismatch"],
    )

    with pytest.raises(RuntimeError, match="BGE runtime contract failed"):
        asyncio.run(
            _BENCHMARK.benchmark_report(
                _BENCHMARK.DEFAULT_DATASET,
                scorer_name="bge",
            )
        )

    if constructed:
        raise AssertionError("BGE scorer must not be constructed before runtime validation")
    if scorer.called:
        raise AssertionError("BGE scoring must not start before runtime validation")


def test_bge_runtime_provenance_rejects_noncanonical_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_BENCHMARK, "validate_flagembedding_runtime", lambda: [])
    scorer = SimpleNamespace(
        model="other/model",
        artifact_path=None,
        use_fp16=False,
    )

    with pytest.raises(RuntimeError, match="requires model BAAI/bge-reranker-v2-m3"):
        _BENCHMARK._runtime_provenance("bge", scorer)


def test_bge_runtime_provenance_rejects_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    monkeypatch.setattr(_BENCHMARK, "validate_flagembedding_runtime", lambda: [])
    scorer = SimpleNamespace(
        model="BAAI/bge-reranker-v2-m3",
        artifact_path=str(model_dir),
        use_fp16=False,
    )

    with pytest.raises(RuntimeError, match="missing config.json"):
        _BENCHMARK._runtime_provenance("bge", scorer)


def test_bge_runtime_provenance_rejects_directory_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "external-dir"
    target.mkdir()
    (target / "weights.bin").write_bytes(b"weights")
    link = model_dir / "linked-dir"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    monkeypatch.setattr(_BENCHMARK, "validate_flagembedding_runtime", lambda: [])
    scorer = SimpleNamespace(
        model="BAAI/bge-reranker-v2-m3",
        artifact_path=str(model_dir),
        use_fp16=False,
    )

    with pytest.raises(RuntimeError, match="unsupported directory symlink"):
        _BENCHMARK._runtime_provenance("bge", scorer)


def test_deployment_evidence_requires_local_bge_artifact() -> None:
    report = {
        "runtime": {
            "kind": "bge",
            "local_artifact_bound": True,
            "artifact_manifest": {"sha256": "a" * 64},
        }
    }
    _BENCHMARK.require_deployment_evidence(report)


@pytest.mark.parametrize(
    "runtime,match",
    [
        ({"kind": "synthetic"}, "requires --scorer bge"),
        (
            {"kind": "bge", "local_artifact_bound": False, "artifact_manifest": None},
            "requires BGE_RERANKER_PATH",
        ),
        (
            {"kind": "bge", "local_artifact_bound": True, "artifact_manifest": {}},
            "requires a complete local artifact manifest",
        ),
    ],
)
def test_deployment_evidence_rejects_nonqualifying_reports(runtime, match) -> None:
    with pytest.raises(RuntimeError, match=match):
        _BENCHMARK.require_deployment_evidence({"runtime": runtime})
