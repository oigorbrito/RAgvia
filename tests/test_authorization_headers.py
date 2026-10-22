from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import _valid_ops_request
from app.api_key_auth import authorization_header, bearer_credential


def _request(*authorization_values: str) -> Request:
    headers = [
        (b"authorization", value.encode("ascii"))
        for value in authorization_values
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "http_version": "1.1",
    }
    return Request(scope)


def test_single_authorization_header_is_preserved() -> None:
    credential = uuid4().hex
    request = _request(f"Bearer {credential}")
    if authorization_header(request) != f"Bearer {credential}":
        raise AssertionError("single Authorization header was not preserved")
    if bearer_credential(request) != credential:
        raise AssertionError("single Bearer credential was not parsed")


def test_duplicate_authorization_headers_are_rejected_by_bearer_parser() -> None:
    request = _request(
        f"Bearer {uuid4().hex}",
        f"Bearer {uuid4().hex}",
    )
    if authorization_header(request) is not None:
        raise AssertionError("duplicate Authorization headers must be ambiguous")
    with pytest.raises(HTTPException) as exc_info:
        bearer_credential(request)
    if exc_info.value.status_code != 401:
        raise AssertionError(
            f"expected duplicate Authorization to return 401, got {exc_info.value.status_code}"
        )


def test_ops_rejects_duplicate_authorization_even_with_correct_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = uuid4().hex
    monkeypatch.setenv("RPY_OPS_TOKEN", expected)
    request = _request(
        f"Bearer {expected}",
        f"Bearer {uuid4().hex}",
    )
    if _valid_ops_request(request):
        raise AssertionError("ops endpoint accepted duplicate Authorization headers")


def test_comma_merged_authorization_header_is_rejected() -> None:
    first = uuid4().hex
    second = uuid4().hex
    request = _request(f"Bearer {first}, Bearer {second}")
    if authorization_header(request) is not None:
        raise AssertionError("comma-merged Authorization header must be rejected")
    with pytest.raises(HTTPException) as exc_info:
        bearer_credential(request)
    if exc_info.value.status_code != 401:
        raise AssertionError(
            f"expected merged Authorization to return 401, got {exc_info.value.status_code}"
        )


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
def test_ops_accepts_case_insensitive_bearer_scheme(
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
) -> None:
    token = uuid4().hex
    monkeypatch.setenv("RPY_OPS_TOKEN", token)
    request = _request(f"{scheme} {token}")
    if not _valid_ops_request(request):
        raise AssertionError(f"ops request rejected valid token with scheme {scheme}")
