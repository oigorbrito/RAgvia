"""Stage an existing, completed Judit GET snapshot; never impersonate a webhook.

No Judit/Anthropic calls, completion event, finalizer job or summary publication.
Default is a local, read-only preflight. The --execute gate writes ONLY to the
operator-selected RAGVIA PostgreSQL database and requires explicit tenant scope.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from app.judit import normalize_cnj, parse_event
from app.processes import stage_version


@dataclass(frozen=True)
class ExistingSnapshot:
    code: str
    request_id: str
    response_id: str
    source_sha256: str
    payload: dict[str, Any]
    step_count: int


class SnapshotRejected(ValueError):
    """Do not include the private provider document in error messages."""


def _uuid(value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError
        return value
    except (ValueError, AttributeError, TypeError):
        raise SnapshotRejected("invalid_provider_identifier") from None


def validate_snapshot(raw: bytes, *, expected_cnj: str) -> ExistingSnapshot:
    if len(raw) > 8_000_000 or not raw:
        raise SnapshotRejected("invalid_snapshot_size")
    try:
        document = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError):
        raise SnapshotRejected("invalid_snapshot_json") from None
    if not isinstance(document, dict) or document.get("request_status") != "completed":
        raise SnapshotRejected("request_not_completed")
    items = document.get("page_data")
    if not isinstance(items, list) or len(items) < 1 or len(items) > 8:
        raise SnapshotRejected("invalid_page_data")
    if (document.get("page") != 1 or document.get("all_pages_count") != 1
            or document.get("page_count") != len(items)
            or document.get("all_count") != len(items)):
        raise SnapshotRejected("incomplete_or_inconsistent_pages")
    if any(not isinstance(item, dict) for item in items):
        raise SnapshotRejected("invalid_page_item")
    lawsuit_items = [item for item in items if item.get("response_type") == "lawsuit"]
    if len(lawsuit_items) != 1 or any(
        item.get("response_type") not in {"lawsuit", "summary"} for item in items
    ):
        raise SnapshotRejected("unexpected_response_types")
    item = lawsuit_items[0]
    data = item.get("response_data")
    if not isinstance(data, dict):
        raise SnapshotRejected("missing_lawsuit_data")
    try:
        code = normalize_cnj(str(data["code"]))
        expected = normalize_cnj(expected_cnj)
        digits = code.replace("-", "").replace(".", "")
        if int(digits[7:9]) != 98 - (int(digits[:7] + digits[9:] + "00") % 97):
            raise ValueError
        if code != expected:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise SnapshotRejected("cnj_mismatch_or_invalid_checksum") from None
    try:
        secrecy = int(data["secrecy_level"])
    except (ValueError, TypeError, KeyError):
        raise SnapshotRejected("unknown_secrecy_level") from None
    if secrecy != 0:
        raise SnapshotRejected("restricted_lawsuit")
    steps = data.get("steps")
    if not isinstance(steps, list) or not all(isinstance(x, dict) for x in steps):
        raise SnapshotRejected("invalid_steps")
    if any(step.get("private") is not False for step in steps):
        raise SnapshotRejected("private_or_unknown_step")
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list) or any(
        not isinstance(a, dict) or a.get("private") is not False for a in attachments
    ):
        raise SnapshotRejected("private_or_unknown_attachment")
    request_id = _uuid(item.get("request_id"))
    if any(_uuid(part.get("request_id")) != request_id for part in items):
        raise SnapshotRejected("inconsistent_request_id")
    response_id = _uuid(item.get("response_id"))
    source_sha256 = hashlib.sha256(raw).hexdigest()

    # Keep source facts and the original provider response ID. Deliberately
    # exclude provider summary, user_id, callback_url and all webhook fields.
    # The marker is an OPERATOR-SUPPLIED GET file, not an authenticated callback.
    payload = {
        "response_id": response_id,
        "request_id": request_id,
        "response_type": "lawsuit",
        "response_data": data,
        "cached_response": bool(item.get("cached", False)),
        "ingest_provenance": {
            "kind": "operator_supplied_judit_get_snapshot",
            "original_file_sha256": source_sha256,
            "webhook_authenticated": False,
            "request_status_from_source": "completed",
        },
    }
    try:
        event = parse_event(payload)
        if not event.is_lawsuit_response or event.request_id != request_id:
            raise ValueError
    except (ValueError, TypeError):
        raise SnapshotRejected("provider_lawsuit_parse_failed") from None
    return ExistingSnapshot(code, request_id, response_id, source_sha256, payload, len(steps))


async def stage_snapshot(
    snapshot: ExistingSnapshot, *, database_url: str, tenant_id: UUID
) -> str:
    """Transactional staging only; do not finalize, enqueue, or call providers."""
    conn = await asyncpg.connect(database_url, timeout=10)
    try:
        async with conn.transaction():
            # Serialize imports for this original request even across processes.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                "judit-get:" + snapshot.request_id,
            )
            tenant_exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)", tenant_id
            )
            if not tenant_exists:
                raise SnapshotRejected("tenant_not_found")
            associations = await conn.fetch(
                """
                SELECT tenant_id, process_code, judit_request_id
                FROM tenant_judit_requests
                WHERE (tenant_id=$1 AND process_code=$2) OR judit_request_id=$3
                FOR UPDATE
                """,
                tenant_id, snapshot.code, snapshot.request_id,
            )
            if any(
                row["tenant_id"] != tenant_id
                or row["process_code"] != snapshot.code
                or row["judit_request_id"] != snapshot.request_id
                for row in associations
            ):
                raise SnapshotRejected("existing_request_correlation_conflicts")
            old_version = await conn.fetchrow(
                """
                SELECT source_payload, judit_request_id
                FROM process_versions WHERE judit_response_id=$1 FOR UPDATE
                """,
                snapshot.response_id,
            )
            if old_version is not None:
                stored_payload = old_version["source_payload"]
                if isinstance(stored_payload, str):
                    stored_payload = json.loads(stored_payload)
                provenance = (
                    stored_payload.get("ingest_provenance")
                    if isinstance(stored_payload, dict) else None
                )
                if (not isinstance(provenance, dict)
                        or provenance.get("kind") != "operator_supplied_judit_get_snapshot"
                        or provenance.get("original_file_sha256") != snapshot.source_sha256
                        or old_version["judit_request_id"] != snapshot.request_id):
                    raise SnapshotRejected("response_already_owned_by_other_source")
            if not associations:
                await conn.execute(
                    """
                    INSERT INTO tenant_judit_requests
                        (tenant_id, process_code, judit_request_id, status, completed_at)
                    VALUES ($1,$2,$3,'processing',NULL)
                    """,
                    tenant_id, snapshot.code, snapshot.request_id,
                )
            process_id, version_id = await stage_version(
                conn,
                code=snapshot.code,
                source_request_id=snapshot.response_id,
                cached_response=bool(snapshot.payload["cached_response"]),
                payload=snapshot.payload,
                judit_request_id=snapshot.request_id,
                judit_response_id=snapshot.response_id,
                judit_callback_id=None,
                tenant_id=tenant_id,
            )
            if old_version is None:
                await conn.execute(
                    """
                    INSERT INTO access_log
                        (tenant_id, process_id, process_code, action, metadata)
                    VALUES ($1,$2,$3,'import_judit_get_snapshot',$4::jsonb)
                    """,
                    tenant_id, process_id, snapshot.code,
                    json.dumps({
                        "source_kind": "operator_supplied_judit_get_snapshot",
                        "sha256": snapshot.source_sha256,
                        "response_id_sha256": hashlib.sha256(
                            snapshot.response_id.encode()
                        ).hexdigest(),
                        "authenticated_webhook": False,
                        "provider_calls": 0,
                    }),
                )
        return "staged" if old_version is None else "already_staged_same_capture"
    finally:
        await conn.close()


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--cnj", required=True)
    parser.add_argument("--tenant-id", type=UUID)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--attest-authorized-reuse", action="store_true")
    args = parser.parse_args()
    if args.execute and (
        args.tenant_id is None or not args.attest_authorized_reuse
        or not os.environ.get("DATABASE_URL", "").strip()
    ):
        raise SnapshotRejected("execution_requires_tenant_database_and_attestation")
    snapshot = validate_snapshot(args.file.read_bytes(), expected_cnj=args.cnj)
    status = "validated_no_database_writes"
    if args.execute:
        status = await stage_snapshot(
            snapshot, database_url=os.environ["DATABASE_URL"], tenant_id=args.tenant_id
        )
    print(json.dumps({
        "status": status,
        "source_file_sha256": snapshot.source_sha256,
        "cnj_sha256": hashlib.sha256(snapshot.code.encode()).hexdigest(),
        "judit_request_id_sha256": hashlib.sha256(snapshot.request_id.encode()).hexdigest(),
        "step_count": snapshot.step_count,
        "provider_calls": 0,
        "claude_calls": 0,
        "webhook_delivery_fabricated": False,
        "finalization_enqueued": False,
        "summary_published": False,
    }, sort_keys=True))
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except (SnapshotRejected, OSError) as exc:
        # Our own errors are stable codes; never echo paths/provider records.
        print(json.dumps({"status": "blocked", "reason": (
            str(exc) if isinstance(exc, SnapshotRejected) else "file_access_failed"
        ), "provider_calls": 0, "claude_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
