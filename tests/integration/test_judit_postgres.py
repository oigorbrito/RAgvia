from __future__ import annotations

import os

import asyncpg
import pytest

from app.migrations import migrate
from app.processes import preferred_judit_version, stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(
            "TRUNCATE judit_deliveries, process_summaries, process_steps, "
            "tenant_processes, access_log, process_versions, processes RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_preferred_version_uses_fresh_response_over_cache() -> None:
    assert TEST_DATABASE_URL is not None
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        code = "0000000-00.0000.0.00.0001"
        request_id = "request-fresh-wins"

        _, cached_version = await stage_version(
            conn,
            code=code,
            source_request_id="response-cache",
            cached_response=True,
            payload={"payload": {"response_data": {"code": code, "name": "cached"}}},
            judit_request_id=request_id,
            judit_response_id="response-cache",
            judit_callback_id="callback-cache",
        )
        process_id, fresh_version = await stage_version(
            conn,
            code=code,
            source_request_id="response-fresh",
            cached_response=False,
            payload={"payload": {"response_data": {"code": code, "name": "fresh"}}},
            judit_request_id=request_id,
            judit_response_id="response-fresh",
            judit_callback_id="callback-fresh",
        )

        preferred = await preferred_judit_version(conn, request_id=request_id)
        assert preferred is not None
        assert preferred["process_id"] == process_id
        assert preferred["version_id"] == fresh_version
        assert preferred["version_id"] != cached_version
        assert preferred["source_cached_response"] is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_preferred_version_falls_back_to_cache_when_no_fresh_response() -> None:
    assert TEST_DATABASE_URL is not None
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        code = "0000000-00.0000.0.00.0002"
        request_id = "request-cache-only"
        _, cached_version = await stage_version(
            conn,
            code=code,
            source_request_id="response-cache-only",
            cached_response=True,
            payload={"payload": {"response_data": {"code": code}}},
            judit_request_id=request_id,
            judit_response_id="response-cache-only",
            judit_callback_id="callback-cache-only",
        )

        preferred = await preferred_judit_version(conn, request_id=request_id)
        assert preferred is not None
        assert preferred["version_id"] == cached_version
        assert preferred["source_cached_response"] is True
    finally:
        await conn.close()
