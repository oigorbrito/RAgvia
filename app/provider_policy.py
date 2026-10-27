from __future__ import annotations

import os
import re


_RUN_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{5,95}$")


def provider_one_shot_mode_enabled() -> bool:
    raw = os.environ.get("PROVIDER_ONE_SHOT_MODE", "false").strip().lower()
    if raw in {"", "0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    raise RuntimeError("PROVIDER_ONE_SHOT_MODE must be a boolean")


def provider_one_shot_run_id() -> str | None:
    if not provider_one_shot_mode_enabled():
        return None
    value = os.environ.get("PROVIDER_ONE_SHOT_RUN_ID", "").strip()
    if not _RUN_ID_RE.fullmatch(value):
        raise RuntimeError("PROVIDER_ONE_SHOT_RUN_ID must be a valid private run reference")
    return value
