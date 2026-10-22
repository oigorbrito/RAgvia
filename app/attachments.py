from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence
from uuid import UUID

import asyncpg

AttachmentStatus = Literal["pending", "ready", "unavailable", "corrupt", "unreadable"]
_ALLOWED_STATUSES = {"pending", "ready", "unavailable", "corrupt", "unreadable"}


@dataclass(frozen=True, slots=True)
class AttachmentChunkInput:
    text: str
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _validate_status(status: str) -> AttachmentStatus:
    normalized = status.strip().lower()
    if normalized not in _ALLOWED_STATUSES:
        raise ValueError(f"invalid attachment status: {status!r}")
    return normalized  # type: ignore[return-value]


def _validate_chunk(chunk: AttachmentChunkInput) -> None:
    if not chunk.text:
        raise ValueError("attachment chunk text must not be empty")
    if (chunk.page_start is None) != (chunk.page_end is None):
        raise ValueError("attachment chunk page bounds must be provided together")
    if chunk.page_start is not None and (
        chunk.page_start < 1 or chunk.page_end is None or chunk.page_end < chunk.page_start
    ):
        raise ValueError("invalid attachment chunk page bounds")
    if (chunk.char_start is None) != (chunk.char_end is None):
        raise ValueError("attachment chunk character bounds must be provided together")
    if chunk.char_start is not None and (
        chunk.char_start < 0 or chunk.char_end is None or chunk.char_end < chunk.char_start
    ):
        raise ValueError("invalid attachment chunk character bounds")


def _manifest_item(
    item: dict[str, Any],
) -> tuple[str, str | None, datetime | None, str | None]:
    source_id = str(item.get("attachment_id") or "").strip()
    if not source_id:
        raise ValueError("attachment manifest item missing attachment_id")
    raw_name = item.get("attachment_name")
    source_name = str(raw_name).strip() if raw_name is not None else None
    source_date = item.get("attachment_date")
    if source_date is not None and not isinstance(source_date, datetime):
        raise ValueError("attachment manifest attachment_date must be datetime or null")
    raw_status = item.get("provider_status")
    provider_status = str(raw_status).strip().lower() if raw_status is not None else None
    return source_id, source_name or None, source_date, provider_status or None


async def sync_attachment_manifest(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    attachments: Sequence[dict[str, Any]],
) -> int:
    """Persist source attachment metadata without resetting local processing state."""
    normalized: list[tuple[str, str | None, datetime | None, str | None]] = []
    seen: set[str] = set()
    for item in attachments:
        source_id, source_name, source_date, provider_status = _manifest_item(item)
        if source_id in seen:
            continue
        seen.add(source_id)
        normalized.append((source_id, source_name, source_date, provider_status))

    if normalized:
        await conn.executemany(
            """
            INSERT INTO process_attachments (
                process_id, version_id, source_attachment_id, status,
                source_name, source_date, provider_status
            ) VALUES ($1, $2, $3, 'pending', $4, $5, $6)
            ON CONFLICT (version_id, source_attachment_id)
            DO UPDATE SET
                source_name = EXCLUDED.source_name,
                source_date = EXCLUDED.source_date,
                provider_status = EXCLUDED.provider_status,
                updated_at = NOW()
            WHERE process_attachments.process_id = EXCLUDED.process_id
            """,
            [
                (process_id, version_id, source_id, source_name, source_date, provider_status)
                for source_id, source_name, source_date, provider_status in normalized
            ],
        )
    return len(normalized)


async def upsert_attachment_state(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    source_attachment_id: str,
    status: AttachmentStatus | str,
    content_type: str | None = None,
    byte_size: int | None = None,
    content_sha256: str | None = None,
    error_code: str | None = None,
) -> UUID:
    """Create/update safe attachment metadata without storing provider payload or bytes."""
    source_id = source_attachment_id.strip()
    if not source_id:
        raise ValueError("source_attachment_id is required")
    normalized_status = _validate_status(str(status))
    if byte_size is not None and byte_size < 0:
        raise ValueError("byte_size must be non-negative")
    if content_sha256 is not None:
        digest = content_sha256.strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")
        content_sha256 = digest

    row = await conn.fetchrow(
        """
        INSERT INTO process_attachments (
            process_id, version_id, source_attachment_id, status,
            content_type, byte_size, content_sha256, error_code
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (version_id, source_attachment_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            content_type = EXCLUDED.content_type,
            byte_size = EXCLUDED.byte_size,
            content_sha256 = EXCLUDED.content_sha256,
            error_code = EXCLUDED.error_code,
            updated_at = NOW()
        WHERE process_attachments.process_id = EXCLUDED.process_id
        RETURNING id
        """,
        process_id,
        version_id,
        source_id,
        normalized_status,
        content_type.strip() if content_type and content_type.strip() else None,
        byte_size,
        content_sha256,
        error_code.strip() if error_code and error_code.strip() else None,
    )
    if row is None:
        raise ValueError("attachment source id already belongs to another process scope")
    return row["id"]


async def replace_attachment_chunks(
    conn: asyncpg.Connection,
    *,
    attachment_id: UUID,
    process_id: UUID,
    version_id: UUID,
    chunks: Sequence[AttachmentChunkInput],
) -> int:
    """Replace parsed chunks atomically; retries cannot duplicate positions."""
    for chunk in chunks:
        _validate_chunk(chunk)

    async with conn.transaction():
        attachment = await conn.fetchrow(
            """
            SELECT id, status
            FROM process_attachments
            WHERE id=$1 AND process_id=$2 AND version_id=$3
            FOR UPDATE
            """,
            attachment_id,
            process_id,
            version_id,
        )
        if attachment is None:
            raise LookupError("attachment does not exist in process/version scope")

        await conn.execute(
            "DELETE FROM attachment_chunks WHERE attachment_id=$1",
            attachment_id,
        )
        if chunks:
            await conn.executemany(
                """
                INSERT INTO attachment_chunks (
                    attachment_id, process_id, version_id, chunk_index, text,
                    page_start, page_end, char_start, char_end, content_sha256
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                [
                    (
                        attachment_id,
                        process_id,
                        version_id,
                        index,
                        chunk.text,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.char_start,
                        chunk.char_end,
                        chunk.content_sha256,
                    )
                    for index, chunk in enumerate(chunks)
                ],
            )
        await conn.execute(
            """
            UPDATE process_attachments
            SET status='ready', error_code=NULL, updated_at=NOW()
            WHERE id=$1
            """,
            attachment_id,
        )
    return len(chunks)


async def load_authorized_attachment_chunks(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_id: UUID,
    version_id: UUID,
) -> list[dict[str, object]]:
    """Load ready attachment chunks only inside the tenant/process/current-version boundary."""
    rows = await conn.fetch(
        """
        SELECT pa.id AS attachment_id,
               pa.source_attachment_id,
               ac.id AS chunk_id,
               ac.chunk_index,
               ac.text,
               ac.page_start,
               ac.page_end,
               ac.char_start,
               ac.char_end,
               ac.content_sha256
        FROM tenant_processes tp
        JOIN processes p
          ON p.id=tp.process_id
         AND p.id=$2
         AND p.current_version_id=$3
         AND p.secrecy_level=0
        JOIN process_attachments pa
          ON pa.process_id=p.id
         AND pa.version_id=$3
         AND pa.status='ready'
        JOIN attachment_chunks ac
          ON ac.attachment_id=pa.id
         AND ac.process_id=p.id
         AND ac.version_id=$3
        WHERE tp.tenant_id=$1
        ORDER BY pa.source_attachment_id, ac.chunk_index
        """,
        tenant_id,
        process_id,
        version_id,
    )
    return [dict(row) for row in rows]


async def search_authorized_attachment_chunks(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_id: UUID,
    version_id: UUID,
    query: str,
    limit: int = 12,
) -> list[dict[str, object]]:
    """Rank ready chunks only after the tenant/process/current-version boundary is applied."""
    if limit <= 0:
        raise ValueError("attachment retrieval limit must be greater than zero")
    normalized_query = query.strip()
    if not normalized_query:
        return []

    rows = await conn.fetch(
        """
        WITH authorized_chunks AS (
            SELECT pa.id AS attachment_id,
                   pa.source_attachment_id,
                   ac.id AS chunk_id,
                   ac.chunk_index,
                   ac.text,
                   ac.page_start,
                   ac.page_end,
                   ac.char_start,
                   ac.char_end,
                   ac.content_sha256,
                   to_tsvector('portuguese', ac.text) AS document
            FROM tenant_processes tp
            JOIN processes p
              ON p.id=tp.process_id
             AND p.id=$2
             AND p.current_version_id=$3
             AND p.secrecy_level=0
            JOIN process_attachments pa
              ON pa.process_id=p.id
             AND pa.version_id=$3
             AND pa.status='ready'
            JOIN attachment_chunks ac
              ON ac.attachment_id=pa.id
             AND ac.process_id=p.id
             AND ac.version_id=$3
            WHERE tp.tenant_id=$1
        ), q AS (
            SELECT websearch_to_tsquery('portuguese', $4) AS query
        )
        SELECT authorized_chunks.*,
               ts_rank_cd(authorized_chunks.document, q.query) AS lexical_score
        FROM authorized_chunks, q
        WHERE authorized_chunks.document @@ q.query
        ORDER BY lexical_score DESC,
                 source_attachment_id,
                 chunk_index
        LIMIT $5
        """,
        tenant_id,
        process_id,
        version_id,
        normalized_query,
        limit,
    )
    return [dict(row) for row in rows]


async def attachment_status_counts(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, int]:
    rows = await conn.fetch(
        """
        SELECT status, count(*)::int AS count
        FROM process_attachments
        WHERE process_id=$1 AND version_id=$2
        GROUP BY status
        """,
        process_id,
        version_id,
    )
    return {str(row["status"]): int(row["count"]) for row in rows}
