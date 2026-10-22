from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any
from uuid import UUID

from app.db import create_pool
from app.embedding_runtime import get_active_embedding_runtime
from app.embedding_spaces import COHERE_PROVIDER

DEFAULT_BATCH_VERSIONS = 25


async def list_version_batch(
    pool: Any,
    *,
    provider: str,
    model: str,
    limit: int,
    after_version_id: UUID | None = None,
) -> list[Any]:
    if limit <= 0:
        raise ValueError("version batch limit must be positive")

    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH checkpoint AS (
                SELECT created_at, id
                FROM process_versions
                WHERE id = $4
            )
            SELECT pv.id,
                   pv.created_at,
                   count(ps.id) FILTER (
                       WHERE length(trim(coalesce(ps.title, '') || ' ' || ps.text)) > 0
                         AND pse.step_id IS NULL
                   ) AS missing_embeddings
            FROM process_versions pv
            JOIN processes p ON p.id = pv.process_id
            LEFT JOIN process_steps ps ON ps.version_id = pv.id
            LEFT JOIN process_step_embeddings pse
              ON pse.step_id = ps.id
             AND pse.provider = $1
             AND pse.model = $2
            WHERE pv.finalized = TRUE
              AND ($1 <> 'cohere' OR coalesce(p.secrecy_level, 0) = 0)
              AND (
                  $4::uuid IS NULL
                  OR (pv.created_at, pv.id) > (
                      (SELECT created_at FROM checkpoint),
                      (SELECT id FROM checkpoint)
                  )
              )
            GROUP BY pv.id, pv.created_at
            ORDER BY pv.created_at ASC, pv.id ASC
            LIMIT $3
            """,
            provider,
            model,
            limit,
            after_version_id,
        )


async def reindex_batch(
    pool: Any,
    *,
    runtime: Any,
    limit: int = DEFAULT_BATCH_VERSIONS,
    after_version_id: UUID | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = await list_version_batch(
        pool,
        provider=runtime.space.provider,
        model=runtime.space.model,
        limit=limit,
        after_version_id=after_version_id,
    )

    versions_seen = 0
    versions_with_missing = 0
    embeddings_written = 0
    last_version_id: str | None = None
    for row in rows:
        version_id = UUID(str(row["id"]))
        missing = int(row["missing_embeddings"] or 0)
        versions_seen += 1
        last_version_id = str(version_id)
        if missing <= 0:
            continue
        versions_with_missing += 1
        if not dry_run:
            embeddings_written += await runtime.ensure_step_embeddings(
                pool,
                version_id=version_id,
            )

    return {
        "space": runtime.space.key,
        "dry_run": dry_run,
        "versions_seen": versions_seen,
        "versions_with_missing": versions_with_missing,
        "embeddings_written": embeddings_written,
        "next_after_version_id": last_version_id,
        "has_more": len(rows) == limit,
    }


async def reindex_all(
    pool: Any,
    *,
    runtime: Any,
    limit: int = DEFAULT_BATCH_VERSIONS,
    dry_run: bool = False,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("version batch limit must be positive")

    after_version_id: UUID | None = None
    batches = 0
    versions_seen = 0
    versions_with_missing = 0
    embeddings_written = 0

    while True:
        report = await reindex_batch(
            pool,
            runtime=runtime,
            limit=limit,
            after_version_id=after_version_id,
            dry_run=dry_run,
        )
        batches += 1
        versions_seen += int(report["versions_seen"])
        versions_with_missing += int(report["versions_with_missing"])
        embeddings_written += int(report["embeddings_written"])

        next_after = report.get("next_after_version_id")
        if not report.get("has_more"):
            return {
                "space": runtime.space.key,
                "dry_run": dry_run,
                "batches": batches,
                "versions_seen": versions_seen,
                "versions_with_missing": versions_with_missing,
                "embeddings_written": embeddings_written,
                "completed": True,
                "last_version_id": next_after,
            }

        if not next_after:
            raise RuntimeError("reindex checkpoint did not advance")
        candidate = UUID(str(next_after))
        if candidate == after_version_id:
            raise RuntimeError("reindex checkpoint repeated without progress")
        after_version_id = candidate


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    database_url = str(args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    runtime = get_active_embedding_runtime()
    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        if args.run_all:
            if args.after_version is not None:
                raise RuntimeError("--after-version cannot be combined with --all")
            return await reindex_all(
                pool,
                runtime=runtime,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        return await reindex_batch(
            pool,
            runtime=runtime,
            limit=args.limit,
            after_version_id=args.after_version,
            dry_run=args.dry_run,
        )
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reindex one resumable batch into the active embedding space"
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_VERSIONS)
    parser.add_argument("--after-version", type=UUID, default=None)
    parser.add_argument(
        "--all",
        dest="run_all",
        action="store_true",
        help="Process resumable batches until the active embedding space is complete",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
