from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_staging_acceptance_record.py"
_SPEC = importlib.util.spec_from_file_location("validate_staging_acceptance_record", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


def _record() -> dict:
    digest = "ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64
    return {
        "schema_version": 1,
        "deployed_image": digest,
        "rollback_image": digest,
        "migration_succeeded": True,
        "public_health_ok": True,
        "public_ready_ok": True,
        "browser_api_request_succeeded": True,
        "callback_reached_ingress": True,
        "callback_staged_version": True,
        "tenant_access_granted": True,
        "terminal_completion_observed": False,
        "finalization_completed": True,
        "summary_validated": True,
        "backup_off_host": True,
        "restore_succeeded": True,
        "attachments_enabled": False,
        "datajud_enabled": False,
        "judit_paid_request_count": 1,
        "evidence_references": ["staging-log-2026-10-05", "restore-drill-2026-10-05"],
    }


def test_complete_record_passes() -> None:
    assert module.validate_record(_record()) == []


def test_second_paid_judit_request_is_rejected() -> None:
    record = _record()
    record["judit_paid_request_count"] = 2
    assert "judit_paid_request_count must remain exactly 1" in module.validate_record(record)


def test_nonimmutable_image_is_rejected() -> None:
    record = _record()
    record["deployed_image"] = "ghcr.io/oigorbrito/rpy:latest"
    assert any("deployed_image" in error for error in module.validate_record(record))


def test_missing_restore_evidence_is_rejected() -> None:
    record = _record()
    record["restore_succeeded"] = False
    assert "restore_succeeded must be true" in module.validate_record(record)


def test_secret_bearing_field_is_rejected() -> None:
    record = _record()
    record["webhook_" + "token"] = "x"
    assert "secret-bearing fields are prohibited" in module.validate_record(record)
