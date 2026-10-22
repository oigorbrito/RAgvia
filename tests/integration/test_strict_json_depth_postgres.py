"""Read-only codec evidence on a disposable TEST_DATABASE_URL, never production."""
from __future__ import annotations

import json
import os

import asyncpg
import pytest

from app.db import _encode_json

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_rejected_nested_json_text_roundtrips_as_a_jsonb_string(
    parser_rejected_json_depth: int,
) -> None:
    if not TEST_DATABASE_URL:
        raise RuntimeError("A disposable TEST_DATABASE_URL is required")
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.set_type_codec(
            "jsonb", schema="pg_catalog", encoder=_encode_json, decoder=json.loads,
            format="text",
        )
        depth = parser_rejected_json_depth
        raw = "[" * depth + "0" + "]" * depth
        kind = await conn.fetchval("SELECT jsonb_typeof($1::jsonb)", raw)
        decoded = await conn.fetchval("SELECT $1::jsonb", raw)
        if kind != "string" or decoded != raw:
            raise AssertionError("Rejected JSON text did not roundtrip as a JSONB string")
        valid = {"nested": [True, None, 1]}
        if await conn.fetchval("SELECT $1::jsonb", valid) != valid:
            raise AssertionError("Valid native JSON contract changed")
    finally:
        await conn.close()
