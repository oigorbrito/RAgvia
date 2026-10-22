from __future__ import annotations

import argparse
import asyncio
import os

from app.migrations import migrate


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Apply Rpy SQL migrations")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    await migrate(args.database_url)


if __name__ == "__main__":
    asyncio.run(_main())
