import pytest

from app.process_requests import configured_judit_provider_call_limit


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("100", 100)])
def test_judit_provider_call_limit_accepts_bounded_integer(monkeypatch, value, expected):
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS", value)
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "true" if value == "1" else "false")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", "APR-SYNTHETIC-RUN")
    assert configured_judit_provider_call_limit() == expected


@pytest.mark.parametrize("value", ["0", "101", "one", "1.5", ""])
def test_judit_provider_call_limit_rejects_invalid_value(monkeypatch, value):
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS", value)
    with pytest.raises(ValueError, match="JUDIT_MAX_PROVIDER_CALLS"):
        configured_judit_provider_call_limit()


def test_one_shot_mode_requires_persisted_limit_one(monkeypatch):
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "true")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", "APR-SYNTHETIC-RUN")
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS", "100")
    with pytest.raises(ValueError, match="PROVIDER_ONE_SHOT_MODE requires"):
        configured_judit_provider_call_limit()
    monkeypatch.setenv("JUDIT_MAX_PROVIDER_CALLS", "1")
    assert configured_judit_provider_call_limit() == 1
