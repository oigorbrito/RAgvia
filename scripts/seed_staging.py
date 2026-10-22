from __future__ import annotations

import asyncio
import os
import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from uuid import UUID
import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.local_demo import DEMO_CODE, seed_demo

STAGING_URL = os.environ.get("RPY_STAGING_DATABASE_URL", "postgresql://127.0.0.1:5435/rpy_staging")
TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
CODE_STORED = DEMO_CODE
CODE_CONCURRENT = "0000002-00.2026.8.21.0001"


async def main():
    token_a = os.environ["RPY_STAGING_TOKEN_A"]
    token_b = os.environ["RPY_STAGING_TOKEN_B"]
    os.environ["RPY_DEMO_BEARER_TOKEN"] = token_a
    os.environ["RPY_BEARER_TOKENS"] = json.dumps(
        {token_a: str(TENANT_A), token_b: str(TENANT_B)}
    )

    # Run official project seed for demo process
    # The reusable demo seeder prints its configured bearer token; suppress that
    # output in this credential-bearing staging process.
    with redirect_stdout(StringIO()):
        await seed_demo(STAGING_URL)

    conn = await asyncpg.connect(STAGING_URL)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            TENANT_B,
            "Tenant B",
        )

        # Seed initial completed tenant_judit_request for DEMO_CODE
        await conn.execute(
            """
            INSERT INTO tenant_judit_requests (
                tenant_id, process_code, judit_request_id, status, attempt_number, created_at, completed_at
            )
            VALUES ($1, $2, $3, $4, 1, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days')
            ON CONFLICT (tenant_id, process_code) DO UPDATE SET
                status = 'completed',
                judit_request_id = 'judit_req_initial_123',
                completed_at = NOW() - INTERVAL '2 days'
            """,
            TENANT_A,
            CODE_STORED,
            "judit_req_initial_123",
            "completed",
        )

        # Clear previous test artifacts for clean idempotent execution
        await conn.execute("DELETE FROM acquisition_authorizations WHERE tenant_id = $1", TENANT_A)
        await conn.execute("DELETE FROM tenant_judit_request_history WHERE tenant_id = $1", TENANT_A)
        await conn.execute("DELETE FROM tenant_judit_requests WHERE tenant_id = $1 AND process_code = $2", TENANT_A, CODE_CONCURRENT)
        await conn.execute("DELETE FROM jobs WHERE task_name = 'request_judit_process'")

        print("Seeding completed successfully with official demo process!")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
