from __future__ import annotations

import asyncio
import os

import pytest
from hypothesis import given, settings, strategies as st

from app.http_limits import (
    DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES,
    InboundPostBodyLimitMiddleware,
    JuditWebhookBodyLimitMiddleware,
    judit_webhook_max_body_bytes,
)


def _scope(
    *,
    content_length: int | None = None,
    method: str = "POST",
    path: str = "/webhooks/judit/token",
) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "query_string": b"",
    }


async def _run(middleware, scope: dict, messages: list[dict]) -> list[dict]:
    queue = list(messages)
    sent: list[dict] = []

    async def receive():
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def test_webhook_body_limit_has_bounded_default(monkeypatch) -> None:
    monkeypatch.delenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", raising=False)
    assert judit_webhook_max_body_bytes() == DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES
    assert DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES == 5 * 1024 * 1024


def test_webhook_body_limit_rejects_invalid_configuration(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        judit_webhook_max_body_bytes()


@pytest.mark.asyncio
async def test_content_length_over_limit_is_rejected_before_app(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = JuditWebhookBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=11),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert called is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_chunked_body_over_limit_is_rejected_without_content_length(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = JuditWebhookBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(),
        [
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"78901", "more_body": False},
        ],
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_at_limit_reaches_app(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")

    async def app(scope, receive, send):
        message = await receive()
        assert message["body"] == b"1234567890"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = JuditWebhookBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=10),
        [{"type": "http.request", "body": b"1234567890", "more_body": False}],
    )

    assert sent[0]["status"] == 204


@settings(max_examples=96, deadline=None)
@given(
    chunks=st.lists(st.binary(min_size=0, max_size=16), min_size=1, max_size=8),
    limit=st.integers(min_value=1, max_value=64),
    include_underdeclared_length=st.booleans(),
)
def test_streamed_webhook_limit_is_invariant_to_chunk_boundaries(
    chunks: list[bytes],
    limit: int,
    include_underdeclared_length: bool,
) -> None:
    total = sum(len(chunk) for chunk in chunks)
    previous = os.environ.get("JUDIT_WEBHOOK_MAX_BODY_BYTES")
    os.environ["JUDIT_WEBHOOK_MAX_BODY_BYTES"] = str(limit)

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    try:
        declared_length = min(total, limit) if include_underdeclared_length else None
        middleware = JuditWebhookBodyLimitMiddleware(app)
        messages = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }
            for index, chunk in enumerate(chunks)
        ]
        sent = asyncio.run(
            _run(
                middleware,
                _scope(content_length=declared_length),
                messages,
            )
        )
    finally:
        if previous is None:
            os.environ.pop("JUDIT_WEBHOOK_MAX_BODY_BYTES", None)
        else:
            os.environ["JUDIT_WEBHOOK_MAX_BODY_BYTES"] = previous

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1  # nosec B101
    assert starts[0]["status"] == (413 if total > limit else 204)  # nosec B101


@pytest.mark.asyncio
async def test_public_summary_post_is_rejected_before_route_parsing(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=11, path="/v1/resumos"),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    if called:
        raise AssertionError("oversized public POST reached the route")
    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 413:
        raise AssertionError(f"expected one 413 response, got {starts!r}")


@pytest.mark.asyncio
async def test_public_tracking_post_rejects_underdeclared_stream(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=5, path="/v1/trackings"),
        [
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"78901", "more_body": False},
        ],
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 413:
        raise AssertionError(f"expected streamed 413 response, got {starts!r}")


@pytest.mark.asyncio
async def test_non_post_request_bypasses_body_limiter(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "1")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=100, method="GET", path="/health"),
        [{"type": "http.request", "body": b"x" * 100, "more_body": False}],
    )

    if not called:
        raise AssertionError("non-POST request should bypass the body limiter")
    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 204:
        raise AssertionError(f"expected passthrough 204 response, got {starts!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"5"), (b"content-length", b"5")],
        [(b"content-length", b"5"), (b"content-length", b"6")],
        [(b"content-length", b"5, 5")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"not-a-number")],
        [(b"content-length", b"5"), (b"transfer-encoding", b"chunked")],
    ],
)
async def test_post_rejects_ambiguous_or_invalid_request_framing(
    monkeypatch: pytest.MonkeyPatch,
    headers: list[tuple[bytes, bytes]],
) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    scope = _scope()
    scope["headers"] = headers
    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        scope,
        [{"type": "http.request", "body": b"12345", "more_body": False}],
    )

    if called:
        raise AssertionError("ambiguous request framing reached the application")
    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 400:
        raise AssertionError(f"expected one 400 response, got {starts!r}")


@pytest.mark.asyncio
async def test_single_decimal_content_length_remains_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")

    async def app(scope, receive, send):
        message = await receive()
        if message.get("body") != b"12345":
            raise AssertionError(f"unexpected request body: {message!r}")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=5),
        [{"type": "http.request", "body": b"12345", "more_body": False}],
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 204:
        raise AssertionError(f"expected passthrough 204 response, got {starts!r}")


@pytest.mark.asyncio
async def test_extremely_long_content_length_fails_closed_without_integer_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    scope = _scope()
    scope["headers"] = [(b"content-length", b"9" * 5000)]
    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        scope,
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    if called:
        raise AssertionError("pathological Content-Length reached the application")
    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 413:
        raise AssertionError(f"expected one 413 response, got {starts!r}")


@pytest.mark.asyncio
async def test_non_post_requests_still_reject_ambiguous_framing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    scope = _scope(method="GET", path="/health")
    scope["headers"] = [
        (b"content-length", b"0"),
        (b"transfer-encoding", b"chunked"),
    ]
    middleware = InboundPostBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        scope,
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    if called:
        raise AssertionError("ambiguous GET framing reached the application")
    starts = [message for message in sent if message["type"] == "http.response.start"]
    if len(starts) != 1 or starts[0]["status"] != 400:
        raise AssertionError(f"expected one 400 response, got {starts!r}")
