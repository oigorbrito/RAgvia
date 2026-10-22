from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.browser_origins import BrowserProcessCorsMiddleware, parse_browser_origins


def test_browser_origins_accept_exact_https_origins() -> None:
    assert parse_browser_origins(
        "https://oigorbrito.github.io,https://staging.rpy.test/"
    ) == ["https://oigorbrito.github.io", "https://staging.rpy.test"]


@pytest.mark.parametrize(
    "value",
    [
        "http://oigorbrito.github.io",
        "https://*.github.io",
        "https://oigorbrito.github.io/rpy",
        "https://user:pass@oigorbrito.github.io",
        "https://127.0.0.1",
        "https://localhost",
    ],
)
def test_browser_origins_reject_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_browser_origins(value)


def test_process_cors_is_scoped_and_supports_authorization_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPY_BROWSER_ORIGINS", "https://oigorbrito.github.io")
    app = FastAPI()
    app.add_middleware(BrowserProcessCorsMiddleware)

    @app.get("/processes/{code}")
    async def process(code: str) -> dict[str, str]:
        return {"code": code}

    @app.get("/ops/metrics")
    async def ops() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    preflight = client.options(
        "/processes/123",
        headers={
            "Origin": "https://oigorbrito.github.io",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization, idempotency-key",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://oigorbrito.github.io"
    allowed_headers = preflight.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers  # nosec B101
    assert "idempotency-key" in allowed_headers  # nosec B101

    process_response = client.get(
        "/processes/123",
        headers={"Origin": "https://oigorbrito.github.io"},
    )
    assert process_response.headers["access-control-allow-origin"] == "https://oigorbrito.github.io"

    ops_response = client.get(
        "/ops/metrics",
        headers={"Origin": "https://oigorbrito.github.io"},
    )
    assert "access-control-allow-origin" not in ops_response.headers
