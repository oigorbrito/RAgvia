from __future__ import annotations

from typing import Any, Awaitable, Callable

from starlette.datastructures import MutableHeaders

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "frame-src 'none'; "
    "form-action 'self'"
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

SECURITY_RESPONSE_HEADERS = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-xss-protection": "0",
    "referrer-policy": "no-referrer",
    "permissions-policy": PERMISSIONS_POLICY,
    "cross-origin-opener-policy": "same-origin",
    "x-permitted-cross-domain-policies": "none",
}


class SecurityResponseHeadersMiddleware:
    """Apply the browser security baseline to every HTTP response."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def security_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_RESPONSE_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, security_send)
