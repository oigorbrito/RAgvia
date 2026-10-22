from __future__ import annotations

import os
from uuid import UUID

from app.json_utils import loads_strict_json

VALIDATE_CARTEIRA_ENV = "RPY_TENANT_PROCESSES"


def configured_webhook_tenant() -> UUID | None:
    """Return the tenant that owns the Judit webhook token, if configured.

    When set, every process ingested through that webhook is bound to the tenant
    automatically (see app.processes.grant_process_access). This closes the
    operational gap in which webhook-ingested processes were never visible to any
    tenant because ``tenant_processes`` is only populated by tests/ops.
    """
    raw = os.environ.get("JUDIT_WEBHOOK_TENANT_ID")
    if not raw or not raw.strip():
        return None
    try:
        return UUID(str(raw).strip())
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError(
            "JUDIT_WEBHOOK_TENANT_ID must be a valid tenant UUID string"
        ) from exc


def parse_carteira_seed() -> dict[UUID, list[str]]:
    """Parse ``RPY_TENANT_PROCESSES`` as {tenant_uuid: [cnjCodes...]}.

    Used by the operational carteira seeding script (scripts/seed_carteira.py)
    and validated eagerly at startup so a misconfigured portfolio cannot reach
    production.
    """
    raw = os.environ.get(VALIDATE_CARTEIRA_ENV)
    if not raw:
        return {}
    try:
        parsed = loads_strict_json(raw)
    except ValueError as exc:
        raise RuntimeError(f"{VALIDATE_CARTEIRA_ENV} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{VALIDATE_CARTEIRA_ENV} must map tenant UUIDs to CNJ lists")

    carteira: dict[UUID, list[str]] = {}
    for tenant_raw, codes in parsed.items():
        try:
            tenant_id = UUID(tenant_raw)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError(
                f"{VALIDATE_CARTEIRA_ENV} contains an invalid tenant UUID: {tenant_raw!r}"
            ) from exc
        if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
            raise RuntimeError(
                f"{VALIDATE_CARTEIRA_ENV} values must be lists of CNJ code strings"
            )
        carteira[tenant_id] = [code.strip() for code in codes if code.strip()]
    return carteira


def validate_carteira_seed() -> None:
    parse_carteira_seed()