from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_production_compose.py"
spec = importlib.util.spec_from_file_location("validate_production_compose", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DIGEST = "ghcr.io/oigorbrito/rpy@sha256:" + ("a" * 64)
OTHER_DIGEST = "ghcr.io/oigorbrito/rpy@sha256:" + ("b" * 64)


def _services(image: str = DIGEST) -> dict:
    return {
        name: {"image": image}
        for name in module.APPLICATION_SERVICES
    }


def test_accepts_one_digest_for_all_application_services() -> None:
    module._validate_application_image(_services())


def test_rejects_mutable_tag() -> None:
    with pytest.raises(SystemExit, match="pinned by sha256 digest"):
        module._validate_application_image(_services("ghcr.io/oigorbrito/rpy:latest"))


def test_rejects_build_on_production_host() -> None:
    services = _services()
    services["api"]["build"] = "."

    with pytest.raises(SystemExit, match="must not build source"):
        module._validate_application_image(services)


def test_rejects_mixed_application_digests() -> None:
    services = _services()
    services["worker-2"]["image"] = OTHER_DIGEST

    with pytest.raises(SystemExit, match="same image digest"):
        module._validate_application_image(services)


def test_rejects_short_or_malformed_digest() -> None:
    with pytest.raises(SystemExit, match="pinned by sha256 digest"):
        module._validate_application_image(
            _services("ghcr.io/oigorbrito/rpy@sha256:1234")
        )
