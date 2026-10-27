"""Fail-closed, one-shot financial approval at the existing Anthropic boundary.

The private approval is trusted operator configuration, not an API request or proof
that the account/external spend control actually exists. Those need human evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import os
from pathlib import Path
import re
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.json_utils import loads_strict_json
from app.provider_policy import provider_one_shot_mode_enabled, provider_one_shot_run_id
from app.tasks import PermanentTaskError

# Public standard global API pricing checked 2026-10-22. No batch/fast/tools.
# Charge every possible input token at the most expensive (1h cache-write) rate,
# using the full documented context window rather than a tokenizer heuristic.
PROPOSED_MODEL = "claude-sonnet-4-6"
INPUT_BOUND = 1_000_000
INPUT_USD_PER_MTOK = Decimal("6")
OUTPUT_USD_PER_MTOK = Decimal("15")
OUTPUT_BOUND = 4000
_REF = re.compile(r"[A-Z][A-Z0-9-]{5,95}")


def _reject() -> None:
    # Never echo approval contents, paths, DSNs or process data in errors.
    raise PermanentTaskError("Anthropic paid generation blocked by preauthorization policy") from None


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(value) if isinstance(value, str) else Decimal("NaN")
    except (InvalidOperation, ValueError):
        _reject()
    if not amount.is_finite() or amount <= 0:
        _reject()
    return amount


@dataclass
class GenerationPermit:
    approval_id: str
    model: str
    tenant_id: UUID
    process_id: UUID
    version_id: UUID
    expires_at: datetime
    max_messages: int
    reserved_usd: Decimal
    document_sha256: str
    database_url: str = field(repr=False)
    one_shot_run_id: str | None = None
    owner_id: UUID = field(default_factory=uuid4)
    started: bool = False

    async def reserve_message(self, request: dict[str, Any]) -> None:
        # Fixed surface only: no tools, images, geography/fast-tier surcharge or
        # caller-supplied headers that would invalidate this conservative estimate.
        if (set(request) != {"model", "max_tokens", "system", "messages", "output_config"}
                or request["model"] != self.model
                or type(request["max_tokens"]) is not int
                or not 0 < request["max_tokens"] <= OUTPUT_BOUND
                or self.expires_at <= datetime.now(timezone.utc)):
            _reject()
        if (len(request["messages"]) != 1 or request["messages"][0].get("role") != "user"
                or not isinstance(request["messages"][0].get("content"), str)
                or len(request["system"]) != 1
                or request["system"][0].get("type") != "text"
                or not isinstance(request["system"][0].get("text"), str)):
            _reject()
        conn = None
        try:
            conn = await asyncpg.connect(self.database_url, timeout=5)
            if not self.started:
                # Commit the full reservation BEFORE any network call. A lost
                # response/crash consumes it; never refund or renew automatically.
                accepted = await conn.fetchval("""
                    INSERT INTO anthropic_generation_reservations (
                        approval_id, owner_id, tenant_id, process_id, version_id,
                        model, reserved_usd, max_messages, messages_reserved,
                        expires_at, document_sha256
                    )
                    SELECT $1,$2,$3,$4,$5,$6,$7,$8,1,$9,$10
                    FROM processes p JOIN tenant_processes tp ON tp.process_id=p.id
                    WHERE p.id=$4 AND p.current_version_id=$5 AND tp.tenant_id=$3
                      AND p.secrecy_level=0 AND $9>NOW()
                      AND (
                          $11::text IS NULL OR EXISTS (
                              SELECT 1
                              FROM judit_one_shot_call_reservations j
                              JOIN tenant_judit_requests a
                                ON a.id=j.tenant_request_id
                              JOIN process_versions source_version
                                ON source_version.process_id=p.id
                               AND source_version.id=p.current_version_id
                               AND source_version.judit_request_id=a.judit_request_id
                              WHERE j.run_id=$11 AND a.tenant_id=$3
                          )
                      )
                    ON CONFLICT (approval_id) DO NOTHING
                    RETURNING approval_id
                    """, self.approval_id, self.owner_id, self.tenant_id, self.process_id,
                    self.version_id, self.model, self.reserved_usd, self.max_messages,
                    self.expires_at, self.document_sha256, self.one_shot_run_id)
                if accepted is None:
                    _reject()
                self.started = True
            else:
                accepted = await conn.fetchval("""
                    UPDATE anthropic_generation_reservations r
                    SET messages_reserved=messages_reserved+1
                    WHERE approval_id=$1 AND owner_id=$2 AND messages_reserved<max_messages
                      AND expires_at>NOW()
                      AND EXISTS (SELECT 1 FROM processes p JOIN tenant_processes tp ON tp.process_id=p.id
                                  WHERE p.id=r.process_id AND p.current_version_id=r.version_id
                                    AND p.secrecy_level=0 AND tp.tenant_id=r.tenant_id
                                    AND ($3::text IS NULL OR EXISTS (
                                        SELECT 1
                                        FROM judit_one_shot_call_reservations j
                                        JOIN tenant_judit_requests a
                                          ON a.id=j.tenant_request_id
                                        JOIN process_versions source_version
                                          ON source_version.process_id=p.id
                                         AND source_version.id=p.current_version_id
                                         AND source_version.judit_request_id=a.judit_request_id
                                        WHERE j.run_id=$3 AND a.tenant_id=r.tenant_id
                                    )))
                    RETURNING approval_id
                    """, self.approval_id, self.owner_id, self.one_shot_run_id)
                if accepted is None:
                    _reject()
        except (asyncpg.PostgresError, OSError, TimeoutError):
            raise PermanentTaskError("Anthropic generation blocked: authorization ledger unavailable") from None
        finally:
            if conn is not None:
                await conn.close()


def load_generation_permit(scope: tuple[UUID, UUID, UUID | None] | None) -> GenerationPermit:
    if os.getenv("ANTHROPIC_PAID_ENABLED") != "true" or scope is None or scope[2] is None:
        _reject()
    if os.getenv("PROVIDER_MAX_ATTEMPTS") != "1":
        _reject()
    model = os.getenv("ANTHROPIC_MODEL")
    if model != PROPOSED_MODEL:
        _reject()
    try:
        path = os.environ["ANTHROPIC_APPROVAL_FILE"]
        raw = Path(path).read_bytes()
        if len(raw) > 8192:
            _reject()
        doc = loads_strict_json(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            _reject()
        for key in ("approval_id", "data_authorization_ref", "account_evidence_ref", "external_limit_ref"):
            if not isinstance(doc.get(key), str) or not _REF.fullmatch(doc[key]):
                _reject()
        if (doc["model"] != model or doc["currency"] != "USD"
                or doc["external_limit_verified"] is not True
                or type(doc["max_provider_attempts"]) is not int or doc["max_provider_attempts"] != 1
                or type(doc["max_regenerations"]) is not int or doc["max_regenerations"] not in (0, 1)):
            _reject()
        one_shot_run_id = None
        if provider_one_shot_mode_enabled():
            one_shot_run_id = provider_one_shot_run_id()
            if doc["max_regenerations"] != 0 or doc["approval_id"] != one_shot_run_id:
                _reject()
        process_id, version_id, tenant_id = scope
        if (UUID(doc["process_id"]), UUID(doc["version_id"]), UUID(doc["tenant_id"])) != scope:
            _reject()
        expires = datetime.fromisoformat(doc["expires_at"])
        now = datetime.now(timezone.utc)
        if expires.tzinfo is None or not now < expires <= now + timedelta(hours=24):
            _reject()
        messages = 1 + doc["max_regenerations"]
        estimate = messages * (INPUT_BOUND * INPUT_USD_PER_MTOK + OUTPUT_BOUND * OUTPUT_USD_PER_MTOK) / Decimal(1_000_000)
        budget = _money(doc["approved_usd"])
        external_limit = _money(doc["external_limit_usd"])
        if not estimate <= external_limit <= budget:
            _reject()
        database_url = os.environ["DATABASE_URL"]
        return GenerationPermit(doc["approval_id"], model, tenant_id, process_id, version_id,
                                expires, messages, estimate, hashlib.sha256(raw).hexdigest(),
                                database_url, one_shot_run_id)
    except (OSError, KeyError, ValueError, TypeError, RuntimeError):
        _reject()
