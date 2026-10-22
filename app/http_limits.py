from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from starlette.responses import JSONResponse

DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES = 5 * 1024 * 1024


def judit_webhook_max_body_bytes() -> int:
    raw = os.getenv(
        "JUDIT_WEBHOOK_MAX_BODY_BYTES",
        str(DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("JUDIT_WEBHOOK_MAX_BODY_BYTES must be an integer") from exc
    if value <= 0:
        raise RuntimeError("JUDIT_WEBHOOK_MAX_BODY_BYTES must be greater than zero")
    return value


class _RequestBodyTooLarge(Exception):
    pass


class InboundPostBodyLimitMiddleware:
    """Bound inbound POST bodies even when Content-Length is absent or forged."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app
        self.limit = judit_webhook_max_body_bytes()

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        headers = list(scope.get("headers", []))
        content_length_values = [
            value for key, value in headers if key.lower() == b"content-length"
        ]
        transfer_encoding_present = any(
            key.lower() == b"transfer-encoding" for key, _ in headers
        )
        if (
            len(content_length_values) > 1
            or (content_length_values and transfer_encoding_present)
        ):
            response = JSONResponse(
                {"detail": "ambiguous request framing"},
                status_code=400,
            )
            await response(scope, receive, send)
            return

        if content_length_values:
            raw_content_length = content_length_values[0]
            if not isinstance(raw_content_length, bytes) or not raw_content_length.isdigit():
                response = JSONResponse(
                    {"detail": "invalid content-length"},
                    status_code=400,
                )
                await response(scope, receive, send)
                return
            if method == "POST":
                normalized_length = raw_content_length.lstrip(b"0") or b"0"
                limit_bytes = str(self.limit).encode("ascii")
                if (
                    len(normalized_length) > len(limit_bytes)
                    or (
                        len(normalized_length) == len(limit_bytes)
                        and normalized_length > limit_bytes
                    )
                ):
                    response = JSONResponse(
                        {"detail": "request body too large"},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return

        if method != "POST":
            await self.app(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            response = JSONResponse(
                {"detail": "request body too large"},
                status_code=413,
            )
            await response(scope, receive, send)


# Backward-compatible name retained for tests/importers while the middleware now
# protects every inbound POST at the same application-level byte boundary.
JuditWebhookBodyLimitMiddleware = InboundPostBodyLimitMiddleware
