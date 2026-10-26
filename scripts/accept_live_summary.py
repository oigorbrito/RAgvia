"""One-shot live Claude acceptance against an already acquired, authorized case.

Uses the actual RAgvia generation, PostgreSQL persistence and authenticated API.
No Judit request is created by this command. No secrets or source text are logged.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import asyncpg
import httpx

from app.db import create_pool
from app.generation_authorization import GenerationPermit, load_generation_permit
from app.rag import generate_summary
from app.tasks import PermanentTaskError


class AcceptanceBlocked(Exception):
    """A stable, payload-free diagnostic code."""


def _api_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise AcceptanceBlocked("invalid_api_url") from exc
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AcceptanceBlocked("invalid_api_url")
    if parsed.path not in ("", "/"):
        raise AcceptanceBlocked("invalid_api_url")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}
    ):
        raise AcceptanceBlocked("api_requires_https_or_loopback")
    return value.rstrip("/")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _check_scope(database_url: str, scope: tuple[UUID, UUID, UUID],
                       permit: GenerationPermit) -> dict[str, Any]:
    process_id, version_id, tenant_id = scope
    connection = await asyncpg.connect(database_url, timeout=5)
    try:
        row = await connection.fetchrow(
            """
            SELECT p.code, p.secrecy_level, p.current_version_id,
                   v.finalized, v.source_request_id,
                   EXISTS(
                       SELECT 1 FROM tenant_processes tp
                       WHERE tp.process_id=p.id AND tp.tenant_id=$3
                   ) AS tenant_authorized,
                   EXISTS(
                       SELECT 1 FROM tenant_judit_requests tjr
                       WHERE tjr.tenant_id=$3 AND tjr.process_code=p.code
                         AND tjr.judit_request_id=v.source_request_id
                   ) AS judit_request_correlated,
                   EXISTS(
                       SELECT 1 FROM process_summaries ps
                       WHERE ps.process_id=p.id AND ps.version_id=$2
                         AND COALESCE((ps.validation->>'passed')::boolean, FALSE)
                   ) AS already_published,
                   EXISTS(
                       SELECT 1 FROM anthropic_generation_reservations r
                       WHERE r.approval_id=$4
                   ) AS approval_consumed
            FROM processes p
            JOIN process_versions v ON v.id=$2 AND v.process_id=p.id
            WHERE p.id=$1
            """,
            process_id, version_id, tenant_id, permit.approval_id,
        )
    finally:
        await connection.close()
    if row is None:
        raise AcceptanceBlocked("process_version_not_found")
    if not row["tenant_authorized"] or row["current_version_id"] != version_id:
        raise AcceptanceBlocked("tenant_or_version_not_authorized")
    if int(row["secrecy_level"] or 0) != 0:
        raise AcceptanceBlocked("secret_process_must_not_call_provider")
    if not row["finalized"] or not str(row["source_request_id"] or "").strip():
        raise AcceptanceBlocked("acquisition_not_finalized")
    if row["judit_request_correlated"] is not True:
        raise AcceptanceBlocked("judit_request_not_correlated_with_tenant")
    if row["already_published"]:
        raise AcceptanceBlocked("summary_already_published_no_repeat_generation")
    if row["approval_consumed"]:
        raise AcceptanceBlocked("approval_already_consumed_no_retry")
    return {"cnj": str(row["code"]), "source_request_recorded": True}


def _assess_publication(*, result: dict[str, Any], stored: dict[str, Any],
                        api_jsx: dict[str, Any], api_json: dict[str, Any],
                        model: str) -> dict[str, Any]:
    """Fail closed on DB/API divergence; never include actual judicial content."""
    text = stored.get("markdown")
    if (result.get("reused") is not False or result.get("persisted") is not True
            or not isinstance(text, str) or not text.strip()):
        raise AcceptanceBlocked("generation_not_newly_persisted")
    if (result.get("validation", {}).get("passed") is not True
            or stored.get("validation", {}).get("passed") is not True
            or stored.get("model") != model):
        raise AcceptanceBlocked("validation_or_model_mismatch")
    if (api_jsx.get("validation", {}).get("passed") is not True
            or api_jsx.get("iaSummary") != text
            or api_jsx.get("format") != "jsx"):
        raise AcceptanceBlocked("api_jsx_not_equal_to_database")
    if (api_json.get("validation", {}).get("passed") is not True
            or api_json.get("format") != "json"
            or not isinstance(api_json.get("iaSummary"), dict)):
        raise AcceptanceBlocked("api_structured_output_not_published")
    if (not isinstance(api_jsx.get("sources"), list)
            or not isinstance(api_jsx.get("claim_evidence"), list)):
        raise AcceptanceBlocked("api_provenance_contract_missing")
    return {
        "document_sha256": _sha(text),
        "document_bytes": len(text.encode("utf-8")),
        "source_count": len(api_jsx["sources"]),
        "claim_evidence_count": len(api_jsx["claim_evidence"]),
        "api_text_parity": True,
        "api_structured_available": True,
        "application_validation_passed": True,
        "independent_legal_quality_qualified": False,
    }


async def _read_persisted(database_url: str, scope: tuple[UUID, UUID, UUID],
                          permit: GenerationPermit) -> tuple[dict[str, Any], int]:
    connection = await asyncpg.connect(database_url, timeout=5)
    try:
        row = await connection.fetchrow(
            """
            SELECT markdown, validation, model
            FROM process_summaries WHERE process_id=$1 AND version_id=$2
            """, scope[0], scope[1],
        )
        reserved = await connection.fetchrow(
            """
            SELECT messages_reserved, max_messages, model
            FROM anthropic_generation_reservations
            WHERE approval_id=$1 AND tenant_id=$2 AND process_id=$3 AND version_id=$4
            """, permit.approval_id, scope[2], scope[0], scope[1],
        )
    finally:
        await connection.close()
    if row is None or reserved is None or reserved["model"] != permit.model:
        raise AcceptanceBlocked("persisted_summary_or_financial_reservation_missing")
    count = int(reserved["messages_reserved"])
    if not 1 <= count <= permit.max_messages:
        raise AcceptanceBlocked("invalid_financial_reservation_count")
    stored = dict(row)
    validation = stored.get("validation")
    if isinstance(validation, str):
        stored["validation"] = json.loads(validation)
    return stored, count


async def _check_api_identity(base_url: str, bearer: str, tenant_id: UUID) -> None:
    """Verify the bearer and exact tenant BEFORE the one-shot paid generation."""
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=15,
        follow_redirects=False,
    ) as client:
        response = await client.get("/v1/auth/verificar")
    if response.status_code != 200:
        raise AcceptanceBlocked("api_credential_not_authorized")
    try:
        identity = response.json()
    except ValueError as exc:
        raise AcceptanceBlocked("api_identity_invalid_json") from exc
    if (not isinstance(identity, dict)
            or identity.get("ok") is not True
            or identity.get("authenticated") is not True
            or identity.get("tenant_id") != str(tenant_id)):
        raise AcceptanceBlocked("api_tenant_identity_mismatch")


async def _api_read(base_url: str, code: str, bearer: str) -> tuple[dict, dict]:
    headers = {"Authorization": f"Bearer {bearer}"}
    path = "/v1/processos/" + quote(code, safe="") + "/resumo"
    async with httpx.AsyncClient(base_url=base_url, headers=headers,
                                 timeout=15, follow_redirects=False) as client:
        for response_format in ("jsx", "json"):
            response = await client.get(path, params={"format": response_format})
            if response.status_code != 200:
                raise AcceptanceBlocked("api_summary_read_failed")
            payload = response.json()
            if not isinstance(payload, dict):
                raise AcceptanceBlocked("api_summary_not_an_object")
            if response_format == "jsx":
                jsx = payload
            else:
                structured = payload
    return jsx, structured


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    scope = (args.process_id, args.version_id, args.tenant_id)
    if args.execute:
        base_url = _api_base_url(args.api_base_url or "")
        bearer = os.environ.get("RAGVIA_ACCEPTANCE_API_TOKEN", "").strip()
        if not bearer or not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise AcceptanceBlocked("api_or_anthropic_credential_unavailable")
        if (urlsplit(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}
                and os.getenv("RAGVIA_ACCEPTANCE_API_ORIGIN", "").rstrip("/") != base_url):
            raise AcceptanceBlocked("remote_api_origin_not_explicitly_pinned")
        if any(os.getenv(flag, "").strip().lower() in {"1", "true", "yes", "on"}
               for flag in ("DATAJUD_ENABLED", "JUDIT_ATTACHMENTS_ENABLED",
                            "ALLOW_EXTERNAL_EMBEDDINGS", "ALLOW_EXTERNAL_RERANKER")):
            raise AcceptanceBlocked("unrelated_external_provider_must_be_disabled")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise AcceptanceBlocked("database_url_unavailable")

    # All paid-generation preconditions are enforced by the existing shared
    # Anthropic guard, not duplicated or bypassed in this acceptance tool.
    permit = load_generation_permit(scope)
    case = await _check_scope(database_url, scope, permit)
    if not args.execute:
        return {
            "status": "CONFIGURATION_VALID_NOT_LIVE_ACCEPTED",
            "provider_calls": 0, "cost_usd": "0",
            "provider_model": permit.model,
            "source_request_recorded": case["source_request_recorded"],
            "estimated_reserved_usd": str(permit.reserved_usd),
            "approval_consumed": False,
            "account_limit_independently_verified_by_script": False,
        }

    # /v1/capabilities may return HTTP 200 for unauthenticated callers.
    # Validate actual bearer authentication AND tenant scope before spending.
    await _check_api_identity(base_url, bearer, scope[2])

    pool = await create_pool(database_url, min_size=1, max_size=4)
    try:
        result = await generate_summary(pool, scope[0], scope[1], tenant_id=scope[2])
    finally:
        await pool.close()
    if result.get("model") != permit.model:
        raise AcceptanceBlocked("unexpected_generation_model")

    stored, reserved_messages = await _read_persisted(database_url, scope, permit)
    jsx, structured = await _api_read(base_url, case["cnj"], bearer)
    measured = _assess_publication(result=result, stored=stored, api_jsx=jsx,
                                    api_json=structured, model=permit.model)
    return {
        "status": "LIVE_GENERATION_AND_API_CONTRACT_CHECKED",
        "model": permit.model,
        "prompt_version": "process-summary-v7",
        "cnj_sha256": _sha(case["cnj"]),
        "source_request_recorded": case["source_request_recorded"],
        "generation_ms": result.get("generation_ms"),
        "usage": {key: result.get("usage", {}).get(key) for key in (
            "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens"
        ) if result.get("usage", {}).get(key) is not None},
        "provider_reported_cost_usd": result.get("cost_usd"),
        "reserved_usd": str(permit.reserved_usd),
        "financial_messages_reserved": reserved_messages,
        **measured,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-id", type=UUID, required=True)
    parser.add_argument("--version-id", type=UUID, required=True)
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true",
                        help="Enable exactly one authorized generation; no automatic retries")
    parser.add_argument("--api-base-url", help="HTTPS or loopback URL to existing RAgvia API")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
    except (AcceptanceBlocked, PermanentTaskError) as exc:
        code = str(exc) if isinstance(exc, AcceptanceBlocked) else "financial_preauthorization_blocked"
        print(json.dumps({"status": "BLOCKED", "reason": code, "automatic_retry": False}))
        return 2
    except Exception as exc:
        # Failure may happen AFTER reservation; never retry without ledger audit.
        print(json.dumps({"status": "INCOMPLETE", "error_class": type(exc).__name__,
                          "automatic_retry": False, "cost_unknown": True}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
