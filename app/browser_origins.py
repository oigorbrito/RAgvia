from __future__ import annotations

import os
from starlette.middleware.cors import CORSMiddleware

_PROCESS_API_PREFIXES = ("/processes/", "/v1/processos/")


from app.browser_origin_config import parse_browser_origins


def configured_browser_origins() -> list[str]:
    return parse_browser_origins(os.environ.get("RPY_BROWSER_ORIGINS"))


def _browser_api_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _PROCESS_API_PREFIXES)


class BrowserProcessCorsMiddleware:
    """Enable CORS only for browser-facing process APIs."""

    def __init__(self, app) -> None:
        self.app = app
        self._cors = CORSMiddleware(
            app,
            allow_origins=configured_browser_origins(),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Accept", "Content-Type", "Idempotency-Key"],
            max_age=600,
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and _browser_api_path(
            str(scope.get("path") or "")
        ):
            await self._cors(scope, receive, send)
            return
        await self.app(scope, receive, send)
