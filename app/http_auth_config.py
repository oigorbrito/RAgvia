from __future__ import annotations

import os


def _required_http_token(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise RuntimeError(f"{name} is required")
    if value != value.strip() or any(char.isspace() for char in value):
        raise RuntimeError(f"{name} must not contain whitespace")
    return value


def validate_http_auth_config() -> None:
    """Fail startup when HTTP authentication credentials are unusable."""
    _required_http_token("JUDIT_WEBHOOK_TOKEN")
    _required_http_token("RPY_OPS_TOKEN")
