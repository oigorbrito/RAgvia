import pytest

from app.http_auth_config import validate_http_auth_config


def _valid(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "judit-token")
    monkeypatch.setenv("RPY_OPS_TOKEN", "ops-token")


def test_valid_http_auth_configuration_passes(monkeypatch) -> None:
    _valid(monkeypatch)
    validate_http_auth_config()


@pytest.mark.parametrize("name", ["JUDIT_WEBHOOK_TOKEN", "RPY_OPS_TOKEN"])
def test_missing_http_auth_token_fails(monkeypatch, name: str) -> None:
    _valid(monkeypatch)
    monkeypatch.delenv(name)

    with pytest.raises(RuntimeError, match=f"{name} is required"):
        validate_http_auth_config()


@pytest.mark.parametrize("name", ["JUDIT_WEBHOOK_TOKEN", "RPY_OPS_TOKEN"])
def test_empty_http_auth_token_fails(monkeypatch, name: str) -> None:
    _valid(monkeypatch)
    monkeypatch.setenv(name, "")

    with pytest.raises(RuntimeError, match=f"{name} is required"):
        validate_http_auth_config()


@pytest.mark.parametrize("name", ["JUDIT_WEBHOOK_TOKEN", "RPY_OPS_TOKEN"])
@pytest.mark.parametrize("value", [" token", "token ", "token value", "token\tvalue", "token\nvalue"])
def test_whitespace_in_http_auth_token_fails(
    monkeypatch, name: str, value: str
) -> None:
    _valid(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=f"{name} must not contain whitespace"):
        validate_http_auth_config()
