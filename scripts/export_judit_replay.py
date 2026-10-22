from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

from app.judit import normalize_cnj, parse_event

_SCHEMA_VERSION = 1


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: str | None) -> str | None:
    if not value:
        return None
    return f"{prefix}-{_hash(value)[:16]}"


def _redact_response_data(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_response_data(item) for item in value]
    if not isinstance(value, dict):
        return value

    redacted: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).casefold()
        if normalized in {
            "main_document",
            "document",
            "cpf",
            "cnpj",
            "email",
            "phone",
            "telephone",
            "address",
        }:
            redacted[key] = "[redacted]"
            continue
        if normalized in {"name", "party_name", "represented_party_name"} and isinstance(item, str):
            redacted[key] = f"ENTITY-{_hash(item)[:12]}"
            continue
        redacted[key] = _redact_response_data(item)
    return redacted


def sanitize_delivery(raw: dict[str, Any], *, code: str) -> dict[str, Any]:
    event = parse_event(raw)
    body = json.loads(json.dumps(raw))

    body["callback_id"] = _stable_id("callback", event.callback_id)
    if body.get("reference_type") != "tracking" and body.get("reference_id"):
        body["reference_id"] = _stable_id("request", str(body["reference_id"]))

    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    if payload.get("request_id"):
        payload["request_id"] = _stable_id("request", str(payload["request_id"]))
    if payload.get("response_id"):
        payload["response_id"] = _stable_id("response", str(payload["response_id"]))

    response_data = payload.get("response_data")
    if isinstance(response_data, dict):
        response_data = _redact_response_data(response_data)
        if response_data.get("code") or response_data.get("process_code"):
            if "code" in response_data:
                response_data["code"] = code
            if "process_code" in response_data:
                response_data["process_code"] = code
        payload["response_data"] = response_data

    body["payload"] = payload
    return body


async def export_bundle(*, database_url: str, code: str) -> dict[str, Any]:
    canonical = normalize_cnj(code)
    conn = await asyncpg.connect(database_url)
    try:
        request_ids = await conn.fetch(
            """
            SELECT judit_request_id, created_at, completed_at
            FROM tenant_judit_requests
            WHERE process_code = $1
              AND judit_request_id IS NOT NULL
            ORDER BY created_at DESC
            """,
            canonical,
        )
        if not request_ids:
            raise RuntimeError("no completed or in-flight Judit request found for CNJ")

        selected_request_id = str(request_ids[0]["judit_request_id"])
        deliveries = await conn.fetch(
            """
            SELECT callback_id, event_type, raw_payload, received_at
            FROM judit_deliveries
            WHERE request_id = $1
            ORDER BY received_at ASC, callback_id ASC
            """,
            selected_request_id,
        )
        if not deliveries:
            raise RuntimeError("no Judit deliveries found for selected request")

        sanitized = [
            {
                "received_order": index,
                "event_type": str(row["event_type"]),
                "payload": sanitize_delivery(dict(row["raw_payload"]), code=canonical),
            }
            for index, row in enumerate(deliveries, start=1)
        ]

        return {
            "schema_version": _SCHEMA_VERSION,
            "kind": "judit_webhook_replay_bundle",
            "cnj_sha256": _hash(canonical),
            "source_request_id_sha256": _hash(selected_request_id),
            "delivery_count": len(sanitized),
            "deliveries": sanitized,
        }
    finally:
        await conn.close()


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a sanitized Judit webhook replay bundle from the bounded raw-delivery "
            "retention store. The output is suitable for private test reuse but must not be "
            "committed without review."
        )
    )
    parser.add_argument("--cnj", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="Defaults to DATABASE_URL",
    )
    args = parser.parse_args()

    if not str(args.database_url).strip():
        raise RuntimeError("DATABASE_URL is required")

    bundle = await export_bundle(database_url=args.database_url, code=args.cnj)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(args.output),
                "delivery_count": bundle["delivery_count"],
                "cnj_sha256": bundle["cnj_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
