from __future__ import annotations

import httpx
import pytest

from app.api import app

_VALID_BODY = '{"cnj":"0000000-00.2026.8.21.0001","format":"json"}'


async def _post_summary(*, content: str, headers) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/v1/resumos", headers=headers, content=content)


def _require_400(response: httpx.Response, detail: str) -> None:
    if response.status_code != 400:
        raise AssertionError(f"expected 400, got {response.status_code}")
    if response.json().get("detail") != detail:
        raise AssertionError(f"unexpected 400 response: {response.json()!r}")


@pytest.mark.asyncio
async def test_v1_summary_request_rejects_duplicate_json_keys_before_auth() -> None:
    response = await _post_summary(
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "strict-json-duplicate",
        },
        content=(
            '{"cnj":"0000000-00.2026.8.21.0001",'
            '"cnj":"0000000-00.2026.8.21.0002","format":"json"}'
        ),
    )
    _require_400(response, "request body must contain strict JSON")


@pytest.mark.asyncio
async def test_v1_summary_request_preserves_malformed_json_error_shape() -> None:
    response = await _post_summary(
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "malformed-json",
        },
        content='{"cnj":',
    )
    _require_400(response, "invalid JSON payload")


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_v1_summary_request_rejects_non_standard_numeric_constants_before_auth(
    constant: str,
) -> None:
    response = await _post_summary(
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "strict-json-non-finite",
        },
        content=(
            '{"cnj":"0000000-00.2026.8.21.0001","format":"json",'
            f'"unused":{{"score":{constant}}}}}'
        ),
    )
    _require_400(response, "request body must contain strict JSON")


@pytest.mark.asyncio
async def test_v1_summary_request_rejects_duplicate_idempotency_keys_before_auth() -> None:
    response = await _post_summary(
        headers=[
            ("Content-Type", "application/json"),
            ("Idempotency-Key", "first-key"),
            ("Idempotency-Key", "second-key"),
        ],
        content=_VALID_BODY,
    )
    _require_400(response, "Idempotency-Key must appear exactly once")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header_value", "expected_detail"),
    [
        (None, "Idempotency-Key is required"),
        ("   ", "Idempotency-Key is required"),
        ("x" * 256, "Idempotency-Key is too long"),
        ("first-key, second-key", "Idempotency-Key must appear exactly once"),
    ],
)
async def test_v1_summary_request_enforces_idempotency_header_contract_before_auth(
    header_value: str | None,
    expected_detail: str,
) -> None:
    headers: list[tuple[str, str]] = [("Content-Type", "application/json")]
    if header_value is not None:
        headers.append(("Idempotency-Key", header_value))
    response = await _post_summary(headers=headers, content=_VALID_BODY)
    _require_400(response, expected_detail)


@pytest.mark.asyncio
async def test_missing_idempotency_key_fails_before_malformed_body_parsing() -> None:
    response = await _post_summary(
        headers={"Content-Type": "application/json"},
        content='{"cnj":',
    )
    _require_400(response, "Idempotency-Key is required")


@pytest.mark.asyncio
async def test_v1_summary_request_rejects_excessive_nesting_before_auth(
    parser_rejected_json_depth: int,
) -> None:
    depth = parser_rejected_json_depth
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/resumos",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "synthetic-depth-253",
            },
            content='{"unused":' + "[" * depth + "0" + "]" * depth + "}",
        )
    _require_400(response, "request body must contain strict JSON")
