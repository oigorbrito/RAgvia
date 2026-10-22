from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

_IMAGE_RE = re.compile(r"^ghcr\.io/oigorbrito/rpy@sha256:[0-9a-f]{64}$")
_REQUIRED_TRUE_FIELDS = (
    "migration_succeeded",
    "public_health_ok",
    "public_ready_ok",
    "browser_api_request_succeeded",
    "callback_reached_ingress",
    "callback_staged_version",
    "tenant_access_granted",
    "finalization_completed",
    "summary_validated",
    "backup_off_host",
    "restore_succeeded",
)


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    for key in ("deployed_image", "rollback_image"):
        value = str(record.get(key) or "").strip()
        if not _IMAGE_RE.fullmatch(value):
            errors.append(f"{key} must be an immutable Rpy GHCR digest")

    if record.get("attachments_enabled") is not False:
        errors.append("attachments_enabled must be false for initial staging acceptance")
    if record.get("datajud_enabled") is not False:
        errors.append("datajud_enabled must be false unless separately authorized")

    for key in _REQUIRED_TRUE_FIELDS:
        if record.get(key) is not True:
            errors.append(f"{key} must be true")

    request_count = record.get("judit_paid_request_count")
    if request_count != 1:
        errors.append("judit_paid_request_count must remain exactly 1")

    terminal = record.get("terminal_completion_observed")
    if terminal not in {True, False}:
        errors.append("terminal_completion_observed must be boolean")

    refs = record.get("evidence_references")
    if not isinstance(refs, list) or not refs:
        errors.append("evidence_references must be a non-empty list")
    elif any(not isinstance(item, str) or not item.strip() for item in refs):
        errors.append("evidence_references entries must be non-empty strings")

    forbidden = (
        "api_key",
        "password",
        "secret",
        "token",
        "raw_request_id",
        "webhook_token",
    )
    for key in record:
        normalized = str(key).casefold()
        if any(fragment in normalized for fragment in forbidden):
            errors.append("secret-bearing fields are prohibited")
            break

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the non-secret persistent-staging acceptance evidence record."
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the JSON acceptance record from standard input",
    )
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required")

    try:
        raw = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("staging acceptance record: BLOCKED")
        return 1
    if not isinstance(raw, dict):
        print("staging acceptance record: BLOCKED")
        return 1

    errors = validate_record(raw)
    if errors:
        print(f"staging acceptance record: BLOCKED ({len(errors)} validation errors)")
        return 1

    print("staging acceptance record: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
