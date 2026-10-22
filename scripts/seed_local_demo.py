from __future__ import annotations

import asyncio
import os

from app.local_demo import seed_demo


async def _main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    if (
        os.getenv("JUDIT_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    ):
        raise RuntimeError("local demo must run with provider credentials unset")
    await seed_demo(database_url)


if __name__ == "__main__":
    asyncio.run(_main())
