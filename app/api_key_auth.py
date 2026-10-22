from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import HTTPException, Request, Response


@dataclass(frozen=True)
class RequestPrincipal:
    tenant_id: UUID
    api_key_id: UUID | None = None
    api_key_fingerprint: str | None = None
    rate_limit_remaining: int | None = None

    @property
    def uses_api_key(self) -> bool:
        return self.api_key_id is not None


def api_key_hash_and_fingerprint(token: str) -> tuple[str, str]:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest, digest[:16]


def api_key_environment(token: str) -> str | None:
    if token.startswith("sk_live_"):
        return "live"
    if token.startswith("sk_test_"):
        return "test"
    return None


def api_key_deployment_environment() -> str:
    environment = os.environ.get("RPY_API_KEY_ENVIRONMENT", "live").strip().casefold()
    if environment not in {"live", "test"}:
        raise RuntimeError("RPY_API_KEY_ENVIRONMENT must be live or test")
    return environment


def authorization_header(request: Request) -> str | None:
    headers = request.headers
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        values = getlist("authorization")
        if len(values) != 1 or "," in values[0]:
            return None
        return values[0]
    value = headers.get("authorization")
    if value is None:
        return None
    rendered = str(value)
    return None if "," in rendered else rendered


def bearer_credential(request: Request) -> str:
    authorization = authorization_header(request) or ""
    scheme, _, supplied = authorization.partition(" ")
    supplied = supplied.strip()
    if scheme.casefold() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return supplied


async def _consume_rate_limit(
    conn: asyncpg.Connection,
    *,
    api_key_id: UUID,
    limit: int,
) -> tuple[int, int]:
    """Consume one fixed-window request and return (remaining, retry_after_seconds)."""
    await conn.execute(
        """
        INSERT INTO api_key_rate_limits (api_key_id)
        VALUES ($1)
        ON CONFLICT (api_key_id) DO NOTHING
        """,
        api_key_id,
    )
    row = await conn.fetchrow(
        """
        SELECT window_started_at, request_count
        FROM api_key_rate_limits
        WHERE api_key_id = $1
        FOR UPDATE
        """,
        api_key_id,
    )
    if row is None:
        raise RuntimeError("API key rate-limit row was not created")

    now = datetime.now(timezone.utc)
    window_started_at = row["window_started_at"]
    elapsed = (now - window_started_at).total_seconds()
    if elapsed >= 60:
        await conn.execute(
            """
            UPDATE api_key_rate_limits
            SET window_started_at = date_trunc('minute', NOW()),
                request_count = 1,
                updated_at = NOW()
            WHERE api_key_id = $1
            """,
            api_key_id,
        )
        return max(limit - 1, 0), 0

    request_count = int(row["request_count"])
    retry_after = max(1, int(60 - elapsed))
    if request_count >= limit:
        return 0, retry_after

    request_count += 1
    await conn.execute(
        """
        UPDATE api_key_rate_limits
        SET request_count = $2,
            updated_at = NOW()
        WHERE api_key_id = $1
        """,
        api_key_id,
        request_count,
    )
    return max(limit - request_count, 0), 0


async def authenticate_api_key_identity(
    request: Request,
    *,
    token: str,
) -> RequestPrincipal:
    """Authenticate one API key and consume its rate-limit slot without CNJ scope lookup."""
    environment = api_key_environment(token)
    if environment is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    if environment != api_key_deployment_environment():
        raise HTTPException(status_code=401, detail="invalid API key")

    key_hash, expected_fingerprint = api_key_hash_and_fingerprint(token)
    pool: asyncpg.Pool = request.app.state.pool
    denial: HTTPException | None = None
    principal: RequestPrincipal | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            key = await conn.fetchrow(
                """
                SELECT id, tenant_id, key_hash, fingerprint, environment,
                       rate_limit_per_minute, expires_at, revoked_at
                FROM api_keys
                WHERE key_hash = $1
                FOR UPDATE
                """,
                key_hash,
            )
            if key is None:
                denial = HTTPException(status_code=401, detail="invalid API key")
            elif not hmac.compare_digest(str(key["key_hash"]), key_hash):
                denial = HTTPException(status_code=401, detail="invalid API key")
            elif not hmac.compare_digest(str(key["fingerprint"]), expected_fingerprint):
                denial = HTTPException(status_code=401, detail="invalid API key")
            elif str(key["environment"]) != environment:
                denial = HTTPException(status_code=401, detail="invalid API key")
            elif key["revoked_at"] is not None:
                denial = HTTPException(status_code=401, detail="revoked API key")
            elif key["expires_at"] is not None and key["expires_at"] <= datetime.now(timezone.utc):
                denial = HTTPException(status_code=401, detail="expired API key")
            else:
                remaining, retry_after = await _consume_rate_limit(
                    conn,
                    api_key_id=key["id"],
                    limit=int(key["rate_limit_per_minute"]),
                )
                if retry_after:
                    denial = HTTPException(
                        status_code=429,
                        detail="rate limit exceeded",
                        headers={
                            "X-RateLimit-Remaining": "0",
                            "Retry-After": str(retry_after),
                        },
                    )
                else:
                    await conn.execute(
                        "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1",
                        key["id"],
                    )
                    principal = RequestPrincipal(
                        tenant_id=key["tenant_id"],
                        api_key_id=key["id"],
                        api_key_fingerprint=str(key["fingerprint"]),
                        rate_limit_remaining=remaining,
                    )

    if denial is not None:
        raise denial
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    return principal


async def authorize_api_key_process(
    request: Request,
    *,
    principal: RequestPrincipal,
    process_code: str,
) -> RequestPrincipal:
    """Require explicit-CNJ or tenant-portfolio scope for an authenticated API key."""
    if not principal.uses_api_key:
        return principal
    if principal.api_key_id is None:
        raise HTTPException(status_code=401, detail="invalid API key")

    pool: asyncpg.Pool = request.app.state.pool
    allowed = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            key = await conn.fetchrow(
                "SELECT allow_portfolio FROM api_keys WHERE id = $1 AND tenant_id = $2",
                principal.api_key_id,
                principal.tenant_id,
            )
            if key is None:
                raise HTTPException(status_code=401, detail="invalid API key")

            explicit_scope = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM api_key_cnj_scopes
                        WHERE api_key_id = $1 AND process_code = $2
                    )
                    """,
                    principal.api_key_id,
                    process_code,
                )
            )
            portfolio_scope = False
            if bool(key["allow_portfolio"]):
                portfolio_scope = bool(
                    await conn.fetchval(
                        """
                        SELECT EXISTS(
                            SELECT 1
                            FROM tenant_processes tp
                            JOIN processes p ON p.id = tp.process_id
                            WHERE tp.tenant_id = $1 AND p.code = $2
                        )
                        """,
                        principal.tenant_id,
                        process_code,
                    )
                )
            allowed = explicit_scope or portfolio_scope
            if not allowed:
                await conn.execute(
                    """
                    INSERT INTO access_log (
                        tenant_id, process_id, process_code, action, metadata,
                        api_key_id, api_key_fingerprint
                    )
                    VALUES ($1, NULL, $2, 'authorization_denied', '{}'::jsonb, $3, $4)
                    """,
                    principal.tenant_id,
                    process_code,
                    principal.api_key_id,
                    principal.api_key_fingerprint,
                )

    if not allowed:
        raise HTTPException(status_code=404, detail="process not found")
    return principal


async def authenticate_api_key(
    request: Request,
    *,
    token: str,
    process_code: str,
) -> RequestPrincipal:
    principal = await authenticate_api_key_identity(request, token=token)
    return await authorize_api_key_process(
        request,
        principal=principal,
        process_code=process_code,
    )


async def log_principal_access(
    conn: asyncpg.Connection,
    *,
    principal: RequestPrincipal,
    process_id: UUID | None,
    process_code: str,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO access_log (
            tenant_id, process_id, process_code, action, metadata,
            api_key_id, api_key_fingerprint
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        """,
        principal.tenant_id,
        process_id,
        process_code,
        action,
        json.dumps(metadata or {}),
        principal.api_key_id,
        principal.api_key_fingerprint,
    )


def apply_rate_limit_headers(response: Response, principal: RequestPrincipal) -> None:
    if principal.rate_limit_remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(principal.rate_limit_remaining)
