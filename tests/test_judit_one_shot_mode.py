from __future__ import annotations

import json

import pytest

from app import judit_client
from app.judit_client import JuditRequestError
from app.provider_policy import provider_one_shot_mode_enabled

CNJ = "0000000-00.2024.8.26.0100"


class _Response:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit: int) -> bytes:
        return json.dumps({"request_id": "synthetic-request-id"}).encode()


def test_reserved_one_shot_context_allows_only_one_requests_post(monkeypatch):
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "true")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", "APR-SYNTHETIC-RUN")
    monkeypatch.setenv("JUDIT_API_KEY", "synthetic-only")
    calls = []

    class _FakeOpener:
        def open(self, request, timeout):
            calls.append((request.get_method(), request.full_url, timeout))
            return _Response()

    def fake_build_opener(handler):
        assert isinstance(handler, judit_client._RejectProviderRedirect)
        return _FakeOpener()

    monkeypatch.setattr(judit_client.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("one-shot path used redirect-following urlopen"),
    )

    async def exercise():
        with judit_client.reserved_judit_acquisition_call():
            result = await judit_client.create_lawsuit_request(CNJ)
            assert result.request_id == "synthetic-request-id"
            with pytest.raises(JuditRequestError) as exc_info:
                await judit_client.create_lawsuit_request(CNJ)
            assert exc_info.value.error_code == "one_shot_operation_blocked"

    import asyncio

    asyncio.run(exercise())
    assert len(calls) == 1
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/requests/")
    assert judit_client._RejectProviderRedirect().redirect_request(
        None, None, 307, "redirect", {}, "https://other.example/requests/"
    ) is None


@pytest.mark.parametrize(
    "operation",
    [
        lambda: judit_client._provider_request(
            "https://tracking.production.judit.io/tracking",
            method="POST",
            payload={},
            accepted_statuses={201},
        ),
        lambda: judit_client._provider_request(
            "https://requests.prod.judit.io/requests/synthetic-id",
            method="GET",
            accepted_statuses={200},
        ),
        lambda: judit_client._provider_request(
            "https://requests.prod.judit.io/requests?page=1&page_size=1",
            method="GET",
            accepted_statuses={200},
        ),
        lambda: judit_client._download_attachment_sync(
            CNJ, instance=1, attachment_id="synthetic-attachment"
        ),
    ],
)
def test_one_shot_mode_blocks_alternate_judit_operations_before_network(monkeypatch, operation):
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "true")
    monkeypatch.setenv("PROVIDER_ONE_SHOT_RUN_ID", "APR-SYNTHETIC-RUN")
    monkeypatch.setenv("JUDIT_API_KEY", "synthetic-only")
    calls = []
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: calls.append(True),
    )
    with pytest.raises(JuditRequestError, match="one-shot authorization"):
        operation()
    assert calls == []


def test_one_shot_mode_fails_closed_on_invalid_setting(monkeypatch):
    monkeypatch.setenv("PROVIDER_ONE_SHOT_MODE", "sometimes")
    with pytest.raises(RuntimeError, match="PROVIDER_ONE_SHOT_MODE"):
        provider_one_shot_mode_enabled()
