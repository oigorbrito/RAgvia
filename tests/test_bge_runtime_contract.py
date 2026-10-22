from __future__ import annotations

import importlib.util

import pytest

from app import bge_runtime_contract


def test_flagembedding_runtime_accepts_locked_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        bge_runtime_contract,
        "package_version",
        lambda name: bge_runtime_contract.EXPECTED_FLAGEMBEDDING_VERSION,
    )

    if bge_runtime_contract.validate_flagembedding_runtime() != []:
        raise AssertionError("locked FlagEmbedding runtime should be accepted")


def test_flagembedding_runtime_rejects_missing_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    errors = bge_runtime_contract.validate_flagembedding_runtime()

    if errors != ["FlagEmbedding is not installed"]:
        raise AssertionError(f"unexpected missing-module errors: {errors!r}")


@pytest.mark.parametrize("installed_version", ["1.4.1", "1.4.3", "2.0.0"])
def test_flagembedding_runtime_rejects_unlocked_version(
    monkeypatch: pytest.MonkeyPatch,
    installed_version: str,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        bge_runtime_contract,
        "package_version",
        lambda name: installed_version,
    )

    errors = bge_runtime_contract.validate_flagembedding_runtime()

    if not any(
        "FlagEmbedding version must match the locked runtime 1.4.2" in error
        for error in errors
    ):
        raise AssertionError(f"expected locked-version error, got {errors!r}")


def test_flagembedding_runtime_rejects_missing_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    def missing_metadata(name: str) -> str:
        raise bge_runtime_contract.PackageNotFoundError(name)

    monkeypatch.setattr(
        bge_runtime_contract,
        "package_version",
        missing_metadata,
    )

    errors = bge_runtime_contract.validate_flagembedding_runtime()

    if errors != ["FlagEmbedding distribution metadata is unavailable"]:
        raise AssertionError(f"unexpected missing-metadata errors: {errors!r}")
