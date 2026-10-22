from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
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
async def test_operational_metrics_are_aggregate_and_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    app.state.pool = pool
    monkeypatch.setenv("RPY_OPS_TOKEN", "ops-secret")

    old = datetime.now(UTC) - timedelta(minutes=5)
    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.0000.0.00.0301"
    secret_markdown = "sensitive-summary-body-must-not-leak"

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE backup_runs, judit_deliveries, jobs, process_summaries, process_steps, "
                "tenant_processes, process_versions, processes RESTART IDENTITY CASCADE"
            )
            await conn.execute(
                """
                INSERT INTO jobs (task_name, payload, status, run_at, created_at, updated_at)
                VALUES
                  ('a', '{}'::jsonb, 'pending', $1, $1, $1),
                  ('b', '{}'::jsonb, 'dead', NOW(), NOW(), NOW()),
                  ('c', '{}'::jsonb, 'completed', NOW(), NOW(), NOW()),
                  ('d', '{}'::jsonb, 'processing', NOW(), NOW(), NOW())
                """,
                old,
            )
            await conn.execute(
                "UPDATE jobs SET last_heartbeat = NOW() - interval '120 seconds' WHERE task_name = 'd'"
            )
            await conn.execute(
                """
                INSERT INTO backup_runs (archive_name, archive_bytes, sha256)
                VALUES ('rpy.dump', 123, repeat('a', 64))
                """
            )
            await conn.execute(
                """
                INSERT INTO judit_deliveries (callback_id, request_id, event_type, raw_payload)
                VALUES ('obs-callback', 'obs-request', 'lawsuit', '{}'::jsonb)
                """
            )
            await conn.execute(
                "INSERT INTO processes (id, code) VALUES ($1, $2)",
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, source_payload, finalized)
                VALUES ($1, $2, 'obs', '{}'::jsonb, TRUE)
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
                """
                INSERT INTO process_summaries (
                    process_id, version_id, markdown, validation, model, prompt_version, generation_ms
                ) VALUES ($1, $2, $3, $4::jsonb, 'model', 'prompt', 250)
                """,
                process_id,
                version_id,
                secret_markdown,
                {"passed": False, "errors": ["x"]},
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            hidden = await client.get("/ops/metrics")
            wrong = await client.get(
                "/ops/metrics", headers={"Authorization": "Bearer wrong"}
            )
            response = await client.get(
                "/ops/metrics", headers={"Authorization": "Bearer ops-secret"}
            )

        assert hidden.status_code == 404
        assert wrong.status_code == 404
        assert response.status_code == 200
        body = response.json()
        assert body["queue"]["counts"] == {
            "pending": 1,
            "processing": 1,
            "completed": 1,
            "dead": 1,
        }
        assert body["queue"]["oldest_runnable_pending_seconds"] >= 240
        assert body["queue"]["stale_processing"] == 1
        assert body["queue"]["dead_last_24h"] == 1
        assert body["queue"]["dead_by_task_last_24h"] == {"b": 1}
        assert body["summaries"]["total"] == 1
        assert body["summaries"]["validation_failed"] == 1
        assert body["summaries"]["validation_failure_rate"] == 1.0
        assert body["summaries"]["avg_generation_ms"] == 250.0
        assert body["summaries"]["p95_generation_ms"] == 250.0
        assert body["webhooks"]["received_last_hour"] == 1
        assert body["webhooks"]["seconds_since_last"] >= 0
        assert body["backup"]["age_seconds"] >= 0
        assert body["backup"]["last_completed_at"] is not None
        assert body["operational_health"]["status"] == "critical"
        signals = {alert["signal"] for alert in body["operational_health"]["alerts"]}
        assert "stale_processing_jobs" in signals
        assert "dead_jobs_24h" in signals
        serialized = str(body)
        assert code not in serialized
        assert secret_markdown not in serialized
    finally:
        await pool.close()
