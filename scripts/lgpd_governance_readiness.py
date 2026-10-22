from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

REQUIRED_REFERENCES = (
    "legal_basis_reference",
    "provider_roles_reference",
    "retention_policy_reference",
    "authorization_policy_reference",
    "incident_response_reference",
    "data_subject_request_reference",
)

FORBIDDEN_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)


class GovernanceReadinessError(ValueError):
    pass


def _parse_approved_at(value: Any) -> None:
    rendered = str(value or "").strip()
    if not rendered:
        raise GovernanceReadinessError("approved_at is required")
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceReadinessError("approved_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise GovernanceReadinessError("approved_at must include a timezone")


def _scan_for_secret_keys(value: Any) -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                errors.append("secret-bearing field is prohibited")
            errors.extend(_scan_for_secret_keys(child))
    elif isinstance(value, list):
        for child in value:
            errors.extend(_scan_for_secret_keys(child))
    return errors


def validate_approval_record(record: dict[str, Any]) -> list[str]:
    errors = _scan_for_secret_keys(record)

    if record.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    if record.get("mass_indexing_approved") is not True:
        errors.append("mass_indexing_approved must be true")

    approver_role = str(record.get("approver_role") or "").strip()
    if not approver_role:
        errors.append("approver_role is required")

    try:
        _parse_approved_at(record.get("approved_at"))
    except GovernanceReadinessError as exc:
        errors.append(str(exc))

    for key in REQUIRED_REFERENCES:
        if not str(record.get(key) or "").strip():
            errors.append(f"{key} is required")

    purposes = record.get("approved_purposes")
    if not isinstance(purposes, list) or not purposes:
        errors.append("approved_purposes must be a non-empty list")
    elif any(not str(item or "").strip() for item in purposes):
        errors.append("approved_purposes entries must be non-empty strings")

    providers = record.get("provider_authorizations")
    if not isinstance(providers, dict):
        errors.append("provider_authorizations must be an object")
    else:
        for name in ("judit", "datajud", "anthropic", "cohere", "infrastructure"):
            if name not in providers:
                errors.append(f"provider_authorizations.{name} is required")
                continue
            state = str(providers[name]).strip().casefold()
            if state not in {"approved", "not_applicable", "disabled"}:
                errors.append(
                    f"provider_authorizations.{name} must be approved, not_applicable, or disabled"
                )

    return errors


def load_record(raw_text: str) -> dict[str, Any]:
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise GovernanceReadinessError("approval input must contain valid JSON") from exc
    if not isinstance(raw, dict):
        raise GovernanceReadinessError("approval record must be a JSON object")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a private LGPD/data-governance approval record before mass indexing."
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the private approval record JSON from standard input",
    )
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required; private approval records are not accepted by path")

    try:
        record = load_record(sys.stdin.read())
    except GovernanceReadinessError:
        print("LGPD governance readiness: BLOCKED")
        return 1

    errors = validate_approval_record(record)
    if errors:
        print(f"LGPD governance readiness: BLOCKED ({len(errors)} validation errors)")
        return 1

    print("LGPD governance readiness: APPROVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
