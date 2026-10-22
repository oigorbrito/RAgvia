from uuid import UUID, uuid4

import pytest

from app.tenancy import configured_webhook_tenant, parse_carteira_seed


def test_configured_webhook_tenant_parses_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = uuid4()
    monkeypatch.setenv("JUDIT_WEBHOOK_TENANT_ID", str(tenant))
    assert configured_webhook_tenant() == tenant


def test_configured_webhook_tenant_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JUDIT_WEBHOOK_TENANT_ID", raising=False)
    assert configured_webhook_tenant() is None


def test_configured_webhook_tenant_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_TENANT_ID", "not-a-uuid")
    with pytest.raises(RuntimeError, match="JUDIT_WEBHOOK_TENANT_ID"):
        configured_webhook_tenant()


def test_parse_carteira_seed_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RPY_TENANT_PROCESSES", raising=False)
    assert parse_carteira_seed() == {}


def test_parse_carteira_seed_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = str(uuid4())
    monkeypatch.setenv(
        "RPY_TENANT_PROCESSES",
        f'{{"{tenant}": ["0000000-00.0000.0.00.0001", "0000000-00.0000.0.00.0002"]}}',
    )
    parsed = parse_carteira_seed()
    assert list(parsed) == [UUID(tenant)]
    assert parsed[UUID(tenant)] == [
        "0000000-00.0000.0.00.0001",
        "0000000-00.0000.0.00.0002",
    ]


def test_parse_carteira_seed_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPY_TENANT_PROCESSES", "{not-json")
    with pytest.raises(RuntimeError, match="RPY_TENANT_PROCESSES"):
        parse_carteira_seed()


def test_parse_carteira_seed_rejects_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = str(uuid4())
    monkeypatch.setenv("RPY_TENANT_PROCESSES", f'{{"{tenant}": "not-a-list"}}')
    with pytest.raises(RuntimeError, match="lists of CNJ"):
        parse_carteira_seed()

def test_parse_carteira_seed_rejects_duplicate_tenant_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = str(uuid4())
    monkeypatch.setenv(
        "RPY_TENANT_PROCESSES",
        (
            f'{{"{tenant}":["0000000-00.0000.0.00.0001"],'
            f'"{tenant}":["0000000-00.0000.0.00.0002"]}}'
        ),
    )
    with pytest.raises(RuntimeError, match="must be valid JSON"):
        parse_carteira_seed()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_parse_carteira_seed_rejects_non_standard_numeric_constants(
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
) -> None:
    tenant = str(uuid4())
    monkeypatch.setenv(
        "RPY_TENANT_PROCESSES",
        f'{{"{tenant}":["0000000-00.0000.0.00.0001"],"weight":{constant}}}',
    )
    with pytest.raises(RuntimeError, match="must be valid JSON"):
        parse_carteira_seed()
