from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "lgpd_governance_readiness.py"
)
_SPEC = importlib.util.spec_from_file_location("lgpd_governance_readiness", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


def _valid_record() -> dict:
    return {
        "schema_version": 1,
        "mass_indexing_approved": True,
        "approved_at": "2026-10-05T14:00:00-03:00",
        "approver_role": "data-governance authority",
        "approved_purposes": ["authorized portfolio/process summarization"],
        "legal_basis_reference": "GOV-LEGAL-BASIS-001",
        "provider_roles_reference": "GOV-PROVIDERS-001",
        "retention_policy_reference": "GOV-RETENTION-001",
        "authorization_policy_reference": "GOV-AUTHZ-001",
        "incident_response_reference": "GOV-INCIDENT-001",
        "data_subject_request_reference": "GOV-DSR-001",
        "provider_authorizations": {
            "judit": "approved",
            "datajud": "disabled",
            "anthropic": "approved",
            "cohere": "disabled",
            "infrastructure": "approved",
        },
    }


def test_valid_governance_record_is_accepted() -> None:
    assert module.validate_approval_record(_valid_record()) == []


def test_mass_indexing_requires_explicit_approval() -> None:
    record = _valid_record()
    record["mass_indexing_approved"] = False
    assert "mass_indexing_approved must be true" in module.validate_approval_record(record)


def test_references_and_provider_states_are_required() -> None:
    record = _valid_record()
    record["retention_policy_reference"] = ""
    record["provider_authorizations"].pop("judit")
    errors = module.validate_approval_record(record)
    assert "retention_policy_reference is required" in errors
    assert "provider_authorizations.judit is required" in errors


def test_naive_approval_timestamp_is_rejected() -> None:
    record = _valid_record()
    record["approved_at"] = "2026-10-05T14:00:00"
    assert "approved_at must include a timezone" in module.validate_approval_record(record)


def test_secret_bearing_fields_are_rejected() -> None:
    record = _valid_record()
    forbidden_key = "_".join(("provider", "api", "key"))
    record[forbidden_key] = uuid4().hex
    errors = module.validate_approval_record(record)
    assert any("secret-bearing field is prohibited" in error for error in errors)
