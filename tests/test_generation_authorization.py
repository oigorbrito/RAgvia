from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from uuid import UUID

import pytest

from app.generation_authorization import load_generation_permit, PROPOSED_MODEL
from app import providers
from app.tasks import PermanentTaskError

SCOPE = tuple(UUID(int=i) for i in (901, 902, 903))


def approval_document(scope=SCOPE):
    process_id, version_id, tenant_id = scope
    return {
        "approval_id": "APR-SYNTHETIC-02B", "model": PROPOSED_MODEL,
        "process_id": str(process_id), "version_id": str(version_id), "tenant_id": str(tenant_id),
        "data_authorization_ref": "DATA-SYNTHETIC-02B",
        "account_evidence_ref": "ACCOUNT-SYNTHETIC-02B",
        "external_limit_ref": "LIMIT-SYNTHETIC-02B", "external_limit_verified": True,
        "currency": "USD", "approved_usd": "13.00", "external_limit_usd": "13.00",
        "max_provider_attempts": 1, "max_regenerations": 1,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }


def configure_approval(monkeypatch, tmp_path, scope=SCOPE, **changes):
    document = dict(approval_document(scope), **changes)
    path = tmp_path / "synthetic-approval.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "false")
    monkeypatch.setenv("ANTHROPIC_PAID_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_APPROVAL_FILE", str(path))
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", document["approval_id"])
    monkeypatch.setenv("ANTHROPIC_MODEL", PROPOSED_MODEL)
    monkeypatch.setenv("PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused-synthetic-scope")
    return document


def test_one_shot_mode_rejects_regeneration_allowance_before_sdk(monkeypatch, tmp_path):
    configure_approval(monkeypatch, tmp_path, max_regenerations=1)
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "true")
    def forbidden_sdk(**kwargs):
        pytest.fail("one-shot approval with regeneration must not construct the SDK")
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", forbidden_sdk)
    with pytest.raises(PermanentTaskError, match="preauthorization"):
        providers.anthropic_client("synthetic-placeholder", authorization_scope=SCOPE)


def test_one_shot_mode_binds_approval_to_durable_run_id(monkeypatch, tmp_path):
    configure_approval(monkeypatch, tmp_path, max_regenerations=0)
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "true")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", "APR-DIFFERENT-SYNTHETIC-RUN")
    monkeypatch.setattr(
        providers.anthropic,
        "AsyncAnthropic",
        lambda **kwargs: pytest.fail("mismatched run ID must block SDK construction"),
    )
    with pytest.raises(PermanentTaskError, match="preauthorization"):
        providers.anthropic_client("synthetic-placeholder", authorization_scope=SCOPE)


def test_default_cannot_even_construct_sdk(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_PAID_ENABLED", raising=False)
    def forbidden_sdk(**kwargs):
        pytest.fail("unauthorized execution must not reach SDK/network")
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", forbidden_sdk)
    with pytest.raises(PermanentTaskError, match="preauthorization"):
        providers.anthropic_client("synthetic-placeholder", authorization_scope=SCOPE)


@pytest.mark.parametrize("changes", [
    {"approval_id": ""}, {"approval_id": "not an approval"},
    {"model": "claude-sonnet-5"}, {"approved_usd": "1"}, {"approved_usd": "NaN"},
    {"approved_usd": "Infinity"}, {"approved_usd": 13},
    {"external_limit_usd": "20"}, {"external_limit_usd": "1"},
    {"external_limit_verified": False}, {"external_limit_ref": ""},
    {"data_authorization_ref": ""}, {"account_evidence_ref": ""},
    {"max_provider_attempts": 2}, {"max_provider_attempts": True},
    {"max_regenerations": 2}, {"max_regenerations": True},
    {"currency": "BRL"}, {"expires_at": "2020-01-01T00:00:00+00:00"},
    {"expires_at": "2099-01-01T00:00:00+00:00"}, {"version_id": str(UUID(int=999))},
    {"tenant_id": str(UUID(int=999))},
])
def test_incomplete_or_insufficient_approval_never_constructs_sdk(monkeypatch, tmp_path, changes):
    configure_approval(monkeypatch, tmp_path, **changes)
    def forbidden_sdk(**kwargs):
        pytest.fail("blocked preauthorization must not instantiate network client")
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", forbidden_sdk)
    with pytest.raises(PermanentTaskError, match="preauthorization"):
        providers.anthropic_client("synthetic-placeholder", authorization_scope=SCOPE)


def test_approval_estimate_covers_full_context_cache_and_correction(monkeypatch, tmp_path):
    configure_approval(monkeypatch, tmp_path)
    permit = load_generation_permit(SCOPE)
    assert permit.reserved_usd == Decimal("12.12")
    assert permit.max_messages == 2
    assert "postgresql" not in repr(permit)


@pytest.mark.parametrize("scope", [None, (SCOPE[0], SCOPE[1], None)])
def test_unscoped_scripts_cannot_spend(monkeypatch, tmp_path, scope):
    configure_approval(monkeypatch, tmp_path)
    with pytest.raises(PermanentTaskError):
        load_generation_permit(scope)


def test_missing_file_and_retry_override_fail_closed(monkeypatch, tmp_path):
    configure_approval(monkeypatch, tmp_path)
    monkeypatch.setenv("PROVIDER_MAX_ATTEMPTS", "2")
    with pytest.raises(PermanentTaskError):
        load_generation_permit(SCOPE)
    monkeypatch.setenv("PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("ANTHROPIC_APPROVAL_FILE", str(tmp_path / "missing"))
    with pytest.raises(PermanentTaskError):
        load_generation_permit(SCOPE)


def test_offline_preflight_does_not_verify_account_or_make_requests(monkeypatch, tmp_path, capsys):
    from scripts.preflight_claude import main
    configure_approval(monkeypatch, tmp_path, max_regenerations=0)
    monkeypatch.setattr("sys.argv", ["preflight_claude", "--process-id", str(SCOPE[0]),
                                    "--version-id", str(SCOPE[1]), "--tenant-id", str(SCOPE[2])])
    def forbidden(*args, **kwargs):
        pytest.fail("offline preflight attempted provider/database access")
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", forbidden)
    monkeypatch.setattr("app.generation_authorization.asyncpg.connect", forbidden)
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "CONFIGURATION_VALID_NOT_LIVE_ACCEPTED"
    assert report["estimated_reserved_usd"] == "6.06"
    assert report["provider_calls"] == 0 and report["cost_usd"] == "0"


async def test_database_failure_prevents_network_and_is_permanent(monkeypatch, tmp_path):
    from types import SimpleNamespace
    configure_approval(monkeypatch, tmp_path)
    async def forbidden_network(**kwargs):
        pytest.fail("database failure must stop before provider network")
    async def unavailable(*args, **kwargs):
        raise OSError("synthetic database unavailable")
    async def close():
        pass
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=forbidden_network), close=close))
    monkeypatch.setattr("app.generation_authorization.asyncpg.connect", unavailable)
    client = providers.anthropic_client("synthetic-only", authorization_scope=SCOPE)
    with pytest.raises(PermanentTaskError, match="ledger unavailable"):
        await client.messages.create(model=PROPOSED_MODEL, max_tokens=4000,
            system=[{"type": "text", "text": "Synthetic only"}],
            messages=[{"role": "user", "content": "Synthetic only"}], output_config={})
