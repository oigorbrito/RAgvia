from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest

from app.api import app
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_invalid_summary_is_persisted_for_ops_but_never_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    tenant_id = uuid4()
    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.2026.8.21.0114"
    invalid_markdown = "# Resumo inválido\n\nProvavelmente será condenado."

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants
            RESTART IDENTITY CASCADE
            """
        )
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'validation-publication-test')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO processes (id, code) VALUES ($1, $2)",
            process_id,
            code,
        )
        await conn.execute(
            """
            INSERT INTO process_versions (id, process_id, source_payload, finalized)
            VALUES ($1, $2, '{}'::jsonb, TRUE)
            """,
            version_id,
            process_id,
        )
        await conn.execute(
            "UPDATE processes SET current_version_id = $2 WHERE id = $1",
            process_id,
            version_id,
        )
        await conn.execute(
            "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
            tenant_id,
            process_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version, generation_ms
            ) VALUES ($1, $2, $3, $4::jsonb, 'model-test', 'prompt-test', 10)
            """,
            process_id,
            version_id,
            invalid_markdown,
            json.dumps({"passed": False, "errors": ["prognostic language is prohibited"]}),
        )

    app.state.pool = pool
    bearer = "validation-publication-bearer"
    ops = "validation-publication-ops"
    app.state.bearer_tokens = {bearer: tenant_id}
    monkeypatch.setenv("RPY_OPS_TOKEN", ops)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            public = await client.get(
                f"/processes/{code}",
                headers={"Authorization": f"Bearer {bearer}"},
            )
            failed = await client.get(
                "/ops/failed-summaries",
                headers={"Authorization": f"Bearer {ops}"},
            )

        assert public.status_code == 200
        public_body = public.json()
        assert public_body["summary_status"] != "available"
        assert public_body["summary"] is None
        assert public_body["iaSummary"] is None
        assert invalid_markdown not in json.dumps(public_body, ensure_ascii=False)

        assert failed.status_code == 200
        failed_body = failed.json()
        assert failed_body["failed_summaries"]
        assert any(item.get("code") == code for item in failed_body["failed_summaries"])
    finally:
        await pool.close()
