from __future__ import annotations

import hmac
import json
import os
from uuid import UUID

from fastapi import HTTPException, Request

HOMOLOGATION_TEST_TOKEN = "ragv-hml-local-test-2026"

from app.api_key_auth import (
    RequestPrincipal,
    api_key_environment,
    authenticate_api_key,
    authenticate_api_key_identity,
    bearer_credential,
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate JSON object key")
        parsed[key] = value
    return parsed


def configured_bearer_tokens() -> dict[str, UUID]:
    raw = os.environ.get("RPY_BEARER_TOKENS", "{}")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RPY_BEARER_TOKENS must be valid JSON") from exc
    except ValueError as exc:
        raise RuntimeError("RPY_BEARER_TOKENS must not contain duplicate token keys") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("RPY_BEARER_TOKENS must be a JSON object mapping token to tenant UUID")
    if not parsed:
        raise RuntimeError("RPY_BEARER_TOKENS must configure at least one bearer token")

    configured: dict[str, UUID] = {}
    for token, tenant_id in parsed.items():
        if token == HOMOLOGATION_TEST_TOKEN and (
            os.environ.get("RPY_HOMOLOGATION_MODE", "").lower() != "true"
            or os.environ.get("RPY_HOMOLOGATION_BIND_ADDRESS", "") != "127.0.0.1"
        ):
            raise RuntimeError(
                "o token padrão de homologação exige RPY_HOMOLOGATION_MODE=true "
                "e RPY_HOMOLOGATION_BIND_ADDRESS=127.0.0.1"
            )
        if not isinstance(token, str) or not token:
            raise RuntimeError("RPY_BEARER_TOKENS contains an empty bearer token")
        if token != token.strip() or any(char.isspace() for char in token):
            raise RuntimeError(
                "RPY_BEARER_TOKENS bearer tokens must not contain whitespace"
            )
        if "," in token:
            raise RuntimeError(
                "RPY_BEARER_TOKENS bearer tokens must not contain commas"
            )
        if api_key_environment(token) is not None:
            raise RuntimeError(
                "RPY_BEARER_TOKENS legacy tokens must not use sk_live_ or sk_test_ prefixes"
            )
        if not isinstance(tenant_id, str):
            raise RuntimeError("RPY_BEARER_TOKENS tenant ids must be UUID strings")
        try:
            configured[token] = UUID(tenant_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError(
                "RPY_BEARER_TOKENS contains an invalid tenant UUID for one configured token"
            ) from exc
    return configured


def _match_legacy_token(configured: dict[str, UUID], supplied: str) -> UUID | None:
    for token, tenant_id in configured.items():
        if hmac.compare_digest(supplied, token):
            return tenant_id
    return None


def _legacy_tenant_for_token(request: Request, supplied: str) -> UUID:
    configured = getattr(request.app.state, "bearer_tokens", None)
    if configured is not None:
        tenant_id = _match_legacy_token(configured, supplied)
        if tenant_id is not None:
            return tenant_id

    if os.environ.get("RPY_BEARER_TOKENS") is not None:
        refreshed = configured_bearer_tokens()
        tenant_id = _match_legacy_token(refreshed, supplied)
        if tenant_id is not None:
            return tenant_id

    raise HTTPException(status_code=401, detail="invalid bearer token")


def tenant_from_request(request: Request) -> UUID:
    """Return the authenticated tenant for legacy or pre-authorized API-key requests."""
    state = getattr(request, "state", None)
    principal = getattr(state, "principal", None)
    if isinstance(principal, RequestPrincipal):
        return principal.tenant_id

    supplied = bearer_credential(request)
    if api_key_environment(supplied) is not None:
        raise HTTPException(status_code=401, detail="API key requires scoped authentication")
    return _legacy_tenant_for_token(request, supplied)


async def principal_from_request_unscoped(request: Request) -> RequestPrincipal:
    """Authenticate tenant identity without assuming the request path contains a CNJ."""
    supplied = bearer_credential(request)
    if api_key_environment(supplied) is not None:
        return await authenticate_api_key_identity(request, token=supplied)
    return RequestPrincipal(tenant_id=_legacy_tenant_for_token(request, supplied))


async def principal_from_request(
    request: Request,
    *,
    process_code: str,
) -> RequestPrincipal:
    supplied = bearer_credential(request)
    if api_key_environment(supplied) is not None:
        return await authenticate_api_key(
            request,
            token=supplied,
            process_code=process_code,
        )
    return RequestPrincipal(tenant_id=_legacy_tenant_for_token(request, supplied))
