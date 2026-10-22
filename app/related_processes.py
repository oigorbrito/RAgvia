from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

import asyncpg

from app.judit import normalize_cnj
from app.retrieval import Step, load_steps

MAX_RELATED_PROCESS_CODES = 20
RELATED_RECENT_MOVEMENTS = 5


@dataclass(frozen=True, slots=True)
class RelatedProcessContext:
    process_id: UUID
    version_id: UUID
    code: str
    court: str | None
    class_name: str | None
    instance: str | None
    movements: tuple[Step, ...]


def normalize_related_codes(codes: Sequence[str]) -> list[str]:
    """Canonicalize a bounded list of related CNJs while preserving request order."""
    if len(codes) > MAX_RELATED_PROCESS_CODES:
        raise ValueError(
            f"related process lookup accepts at most {MAX_RELATED_PROCESS_CODES} codes"
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for code in codes:
        canonical = normalize_cnj(str(code))
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return normalized


async def load_related_process_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    origin_process_id: UUID,
    related_codes: Sequence[str],
    recent_movements: int = RELATED_RECENT_MOVEMENTS,
) -> list[RelatedProcessContext]:
    """Load related-process context without crossing tenant/process boundaries.

    The caller supplies candidate CNJ codes from an authorized product/source
    contract. This function does not infer relations from raw provider payloads.
    Both the origin and every returned related process must belong to the tenant's
    durable portfolio. Unauthorized or nonexistent candidate codes are omitted so
    the lookup does not become an authorization oracle.

    The returned text is suitable only for tenant-scoped request context. It must
    not be injected into the globally shared process summary because summary rows
    are currently keyed only by process/version, not by tenant.
    """
    if recent_movements <= 0:
        raise ValueError("recent_movements must be positive")

    codes = normalize_related_codes(related_codes)
    if not codes:
        return []

    origin_authorized = bool(
        await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM tenant_processes
                WHERE tenant_id = $1 AND process_id = $2
            )
            """,
            tenant_id,
            origin_process_id,
        )
    )
    if not origin_authorized:
        return []

    rows = await conn.fetch(
        """
        SELECT p.id,
               p.current_version_id,
               p.code,
               p.court,
               p.class_name,
               CASE
                   WHEN jsonb_typeof(p.header->'instance') = 'string'
                   THEN p.header->>'instance'
                   WHEN jsonb_typeof(p.header->'instance') = 'number'
                   THEN p.header->>'instance'
                   ELSE NULL
               END AS instance
        FROM processes p
        JOIN tenant_processes tp
          ON tp.process_id = p.id
         AND tp.tenant_id = $1
        WHERE p.code = ANY($2::text[])
          AND p.id <> $3
          AND p.current_version_id IS NOT NULL
          AND p.secrecy_level = 0
        ORDER BY array_position($2::text[], p.code)
        """,
        tenant_id,
        codes,
        origin_process_id,
    )

    contexts: list[RelatedProcessContext] = []
    for row in rows:
        version_id = row["current_version_id"]
        steps = await load_steps(conn, version_id=version_id)
        recent = tuple(steps[-recent_movements:])
        contexts.append(
            RelatedProcessContext(
                process_id=row["id"],
                version_id=version_id,
                code=str(row["code"]),
                court=row["court"],
                class_name=row["class_name"],
                instance=row["instance"],
                movements=recent,
            )
        )
    return contexts


def safe_related_provenance(
    contexts: Sequence[RelatedProcessContext],
) -> list[dict[str, Any]]:
    """Return traceable related-source metadata without raw payload or movement text."""
    sources: list[dict[str, Any]] = []
    source_order = 0
    for context in contexts:
        if not context.movements:
            sources.append(
                {
                    "kind": "related_process",
                    "process_code": context.code,
                    "step_id": None,
                    "step_number": None,
                    "occurred_at": None,
                    "source_order": source_order,
                }
            )
            source_order += 1
            continue

        for step in context.movements:
            sources.append(
                {
                    "kind": "related_process_movement",
                    "process_code": context.code,
                    "step_id": str(step.id),
                    "step_number": int(step.step_number),
                    "occurred_at": step.occurred_at,
                    "source_order": source_order,
                }
            )
            source_order += 1
    return sources
