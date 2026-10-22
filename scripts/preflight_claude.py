"""Validate declared approval offline. Never instantiate a provider or make requests."""
from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.generation_authorization import load_generation_permit, INPUT_BOUND, OUTPUT_BOUND
from app.tasks import PermanentTaskError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-id", type=UUID, required=True)
    parser.add_argument("--version-id", type=UUID, required=True)
    parser.add_argument("--tenant-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        permit = load_generation_permit((args.process_id, args.version_id, args.tenant_id))
    except PermanentTaskError:
        print(json.dumps({"status": "BLOCKED", "provider_calls": 0, "cost_usd": "0"}))
        return 2
    print(json.dumps({
        "status": "CONFIGURATION_VALID_NOT_LIVE_ACCEPTED", "provider_calls": 0, "cost_usd": "0",
        "model": permit.model, "max_messages": permit.max_messages,
        "input_bound_per_message": INPUT_BOUND, "output_bound_per_message": OUTPUT_BOUND,
        "estimated_reserved_usd": str(permit.reserved_usd),
        "account_access": "requires independent evidence review",
        "external_limit": "operator declaration; must verify actual enforcement and remaining budget",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
