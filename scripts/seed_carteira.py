from __future__ import annotations

import asyncio
import os

from app.db import create_pool
from app.tenancy import parse_carteira_seed


async def seed_carteira(database_url: str) -> None:
    """Bind CNJ codes to tenants as declared in ``RPY_TENANT_PROCESSES``.

    Operational entrypoint for populating the authorized tenant portfolio
    (``tenant_processes``) for onboarding/backfill. Webhook-ingested processes can
    alternatively be bound automatically using ``JUDIT_WEBHOOK_TENANT_ID``.
    """
    carteira = parse_carteira_seed()
    if not carteira:
        print("no RPY_TENANT_PROCESSES declared; nothing to seed")
        return

    pool = await create_pool(database_url, min_size=1, max_size=2)
    bound = skipped = 0
    try:
        async with pool.acquire() as conn:
            for tenant_id, codes in carteira.items():
                for code in codes:
                    process_id = await conn.fetchval(
                        "SELECT id FROM processes WHERE code = $1", code
                    )
                    if process_id is None:
                        print(f"skip {code} (process not ingested yet)")
                        skipped += 1
                        continue
                    inserted = await conn.fetchrow(
                        """
                        INSERT INTO tenant_processes (tenant_id, process_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        RETURNING process_id
                        """,
                        tenant_id,
                        process_id,
                    )
                    if inserted is not None:
                        bound += 1
                    else:
                        skipped += 1
        print(f"carteira seed complete: {bound} bound, {skipped} skipped")
    finally:
        await pool.close()


async def _main() -> None:
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("API_DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL or API_DATABASE_URL is required")
    await seed_carteira(database_url)


if __name__ == "__main__":
    asyncio.run(_main())