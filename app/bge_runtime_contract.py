from __future__ import annotations

import importlib.util
from importlib.metadata import PackageNotFoundError, version as package_version

EXPECTED_FLAGEMBEDDING_VERSION = "1.4.2"


def validate_flagembedding_runtime() -> list[str]:
    if importlib.util.find_spec("FlagEmbedding") is None:
        return ["FlagEmbedding is not installed"]

    try:
        installed_version = package_version("FlagEmbedding")
    except PackageNotFoundError:
        return ["FlagEmbedding distribution metadata is unavailable"]

    if installed_version != EXPECTED_FLAGEMBEDDING_VERSION:
        return [
            "FlagEmbedding version must match the locked runtime "
            f"{EXPECTED_FLAGEMBEDDING_VERSION}, got {installed_version}"
        ]
    return []
