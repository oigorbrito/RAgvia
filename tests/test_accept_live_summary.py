from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import httpx

import pytest

from scripts.accept_live_summary import (
    AcceptanceBlocked, _api_base_url, _assess_publication, _check_api_identity, _run,
)


@pytest.mark.parametrize("value", [
    "", "http://example.org", "https://user:pass@example.org",
    "https://example.org/path", "https://example.org?secret=x",
    "file:///tmp/api", "http://192.168.1.1", "https://example.org/#frag",
])
def test_api_destination_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(AcceptanceBlocked):
        _api_base_url(value)


@pytest.mark.parametrize("value", [
    "https://api.example.org", "http://localhost:8000",
    "http://127.0.0.1:8000",
])
def test_api_destination_allows_https_or_loopback(value: str) -> None:
    assert _api_base_url(value) == value


def _publication() -> dict:
    return {
        "result": {"reused": False, "persisted": True,
                   "validation": {"passed": True}},
        "stored": {"markdown": "Resumo sintético\n", "validation": {"passed": True},
                   "model": "claude-sonnet-4-6"},
        "api_jsx": {"iaSummary": "Resumo sintético\n", "format": "jsx",
                    "validation": {"passed": True}, "sources": [],
                    "claim_evidence": []},
        "api_json": {"iaSummary": {"summary": {}}, "format": "json",
                     "validation": {"passed": True}},
        "model": "claude-sonnet-4-6",
    }


def test_publication_contract_checks_exact_db_api_parity() -> None:
    report = _assess_publication(**_publication())
    assert report["api_text_parity"] is True
    assert report["api_structured_available"] is True
    assert report["independent_legal_quality_qualified"] is False
    assert report["document_bytes"] > 0


@pytest.mark.parametrize("mutation", [
    ("result", "reused", True),
    ("result", "persisted", False),
    ("stored", "markdown", ""),
    ("stored", "model", "fixture-not-a-real-provider"),
    ("api_jsx", "iaSummary", "documento divergente"),
    ("api_jsx", "sources", None),
    ("api_json", "iaSummary", None),
])
def test_publication_contract_rejects_incomplete_evidence(
    mutation: tuple[str, str, object]
) -> None:
    payload = _publication()
    owner, field, value = mutation
    payload[owner][field] = value
    with pytest.raises(AcceptanceBlocked):
        _assess_publication(**payload)


@pytest.mark.asyncio
async def test_execute_missing_credentials_fails_before_db_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID
    import scripts.accept_live_summary as acceptance
    monkeypatch.delenv("RAGVIA_ACCEPTANCE_API_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async def forbidden(*args, **kwargs):
        pytest.fail("missing credentials must never reach network, database or model")
    monkeypatch.setattr(acceptance, "_check_scope", forbidden)
    monkeypatch.setattr(acceptance, "generate_summary", forbidden)
    args = SimpleNamespace(
        process_id=UUID(int=1), version_id=UUID(int=2), tenant_id=UUID(int=3),
        execute=True, api_base_url="https://api.example.org"
    )
    with pytest.raises(AcceptanceBlocked, match="credential_unavailable"):
        await _run(args)


@pytest.mark.asyncio
async def test_preflight_never_calls_model_and_reports_zero_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID
    import scripts.accept_live_summary as acceptance
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic-only")
    monkeypatch.setattr(acceptance, "load_generation_permit", lambda scope:
                        SimpleNamespace(model="claude-sonnet-4-6",
                                        approval_id="APR-TEST", reserved_usd="6.06"))
    async def fake_check(*args, **kwargs):
        return {"cnj": "0000000-00.0000.0.00.0000", "source_request_recorded": True}
    async def forbidden(*args, **kwargs):
        pytest.fail("offline preflight attempted model network")
    monkeypatch.setattr(acceptance, "_check_scope", fake_check)
    monkeypatch.setattr(acceptance, "generate_summary", forbidden)
    args = SimpleNamespace(
        process_id=UUID(int=1), version_id=UUID(int=2), tenant_id=UUID(int=3),
        execute=False, api_base_url=None,
    )
    result = await _run(args)
    assert result["provider_calls"] == 0
    assert result["status"] == "CONFIGURATION_VALID_NOT_LIVE_ACCEPTED"



@pytest.mark.parametrize("status_code,body,expected_error", [
    (401, {"detail": "unauthorized"}, "api_credential_not_authorized"),
    (403, {"detail": "forbidden"}, "api_credential_not_authorized"),
    (200, {"ok": True, "authenticated": True,
           "tenant_id": str(UUID(int=999))}, "api_tenant_identity_mismatch"),
    (200, {"ok": True, "authenticated": False,
           "tenant_id": str(UUID(int=44))}, "api_tenant_identity_mismatch"),
    (200, {"authenticated": True,
           "tenant_id": str(UUID(int=44))}, "api_tenant_identity_mismatch"),
])
@pytest.mark.asyncio
async def test_identity_gate_rejects_unauthorized_or_wrong_tenant(
    monkeypatch: pytest.MonkeyPatch, status_code: int, body: dict, expected_error: str
) -> None:
    import scripts.accept_live_summary as acceptance
    original_client = httpx.AsyncClient
    observed = []

    def respond(req: httpx.Request) -> httpx.Response:
        observed.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(status_code, json=body)

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        acceptance.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    with pytest.raises(AcceptanceBlocked, match=expected_error):
        await _check_api_identity("http://localhost:8080", "synthetic-token", UUID(int=44))
    assert observed == [
        ("http://localhost:8080/v1/auth/verificar", "Bearer synthetic-token")
    ]


@pytest.mark.asyncio
async def test_identity_gate_accepts_matching_authorized_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.accept_live_summary as acceptance
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"ok": True, "authenticated": True, "tenant_id": str(UUID(int=44))}
        )
    )
    monkeypatch.setattr(
        acceptance.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    await _check_api_identity("http://localhost:8080", "synthetic-token", UUID(int=44))


@pytest.mark.asyncio
async def test_bad_api_identity_never_triggers_model_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.accept_live_summary as acceptance
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic-unused")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unused")
    monkeypatch.setenv("RAGVIA_ACCEPTANCE_API_TOKEN", "synthetic-unused")
    monkeypatch.setattr(
        acceptance, "load_generation_permit",
        lambda _: SimpleNamespace(approval_id="APR-SYNTHETIC", model="claude-sonnet-4-6"),
    )

    async def scope_check(*_args, **_kwargs):
        return {"cnj": "0000000-00.0000.0.00.0000", "source_request_recorded": True}

    async def unauthorized(*_args, **_kwargs):
        raise AcceptanceBlocked("api_credential_not_authorized")

    async def forbidden(*_args, **_kwargs):
        pytest.fail("unauthorized API must never trigger paid provider generation")

    monkeypatch.setattr(acceptance, "_check_scope", scope_check)
    monkeypatch.setattr(acceptance, "_check_api_identity", unauthorized)
    monkeypatch.setattr(acceptance, "generate_summary", forbidden)
    args = SimpleNamespace(
        process_id=UUID(int=1), version_id=UUID(int=2), tenant_id=UUID(int=3),
        execute=True, api_base_url="http://localhost:8080",
    )
    with pytest.raises(AcceptanceBlocked, match="api_credential_not_authorized"):
        await _run(args)
