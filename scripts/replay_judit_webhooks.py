from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.judit import normalize_cnj

_SCHEMA_VERSION = 1


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_bundle(path: Path, *, code: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("Judit replay bundle must be a JSON object")
    if document.get("schema_version") != _SCHEMA_VERSION:
        raise RuntimeError("unsupported Judit replay bundle schema")
    if document.get("kind") != "judit_webhook_replay_bundle":
        raise RuntimeError("invalid Judit replay bundle kind")
    canonical = normalize_cnj(code)
    if document.get("cnj_sha256") != _hash(canonical):
        raise RuntimeError("Judit replay bundle does not match the requested CNJ")
    deliveries = document.get("deliveries")
    if not isinstance(deliveries, list) or not deliveries:
        raise RuntimeError("Judit replay bundle has no deliveries")
    return document


def replay_bundle(
    *,
    bundle: dict[str, Any],
    base_url: str,
    webhook_token: str,
    timeout_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    if not webhook_token:
        raise RuntimeError("JUDIT_WEBHOOK_TOKEN is required")
    endpoint = f"{base_url.rstrip('/')}/webhooks/judit/{webhook_token}"
    results: list[dict[str, Any]] = []
    for delivery in bundle["deliveries"]:
        if not isinstance(delivery, dict) or not isinstance(delivery.get("payload"), dict):
            raise RuntimeError("invalid delivery in Judit replay bundle")
        payload = json.dumps(
            delivery["payload"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "rpy-judit-replay/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = int(response.status)
                response.read(65536)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"replay delivery failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("replay delivery transport failed") from None
        if status != 200:
            raise RuntimeError(f"replay delivery failed with HTTP {status}")
        results.append(
            {
                "received_order": delivery.get("received_order"),
                "event_type": delivery.get("event_type"),
                "status": status,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a previously exported sanitized Judit webhook bundle against an Rpy "
            "environment. This performs no Judit provider calls."
        )
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--cnj", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--webhook-token",
        default=os.getenv("JUDIT_WEBHOOK_TOKEN", ""),
        help="Defaults to JUDIT_WEBHOOK_TOKEN",
    )
    args = parser.parse_args()

    bundle = load_bundle(args.bundle, code=args.cnj)
    results = replay_bundle(
        bundle=bundle,
        base_url=args.base_url,
        webhook_token=args.webhook_token,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "provider_network_calls_performed": False,
                "replayed_deliveries": len(results),
                "deliveries": results,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
