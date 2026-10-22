from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from app.claim_evidence import attachment_evidence_ref, movement_evidence_ref
from app.retrieval import RankedStep
from app.unicode_security import model_view_text

_ALLOWED_UNICODE_SECURITY_FLAGS = {
    "bidi_control",
    "zero_width",
    "default_ignorable",
    "mixed_script",
}


def _unicode_flags_json(value: Any) -> str:
    flags = [str(flag) for flag in (value or [])]
    if any(flag not in _ALLOWED_UNICODE_SECURITY_FLAGS for flag in flags):
        raise ValueError("invalid unicode security flag")
    return json.dumps(flags)


def selected_movement_sources(ranked: Sequence[RankedStep]) -> list[dict[str, Any]]:
    """Return text-free provenance for the exact movement context sent to models."""
    sources: list[dict[str, Any]] = []
    for source_order, item in enumerate(ranked):
        text_view = model_view_text(str(item.step.text or ""))
        title_view = model_view_text(str(item.step.title or ""))
        flags = list(dict.fromkeys((*text_view.flags, *title_view.flags)))
        sources.append(
            {
                "evidence_ref": movement_evidence_ref(item.step.id),
                "step_id": item.step.id,
                "step_number": int(item.step.step_number),
                "occurred_at": item.step.occurred_at,
                "source_order": source_order,
                "source_text_sha256": text_view.normalized_sha256,
                "unicode_security_flags": flags,
            }
        )
    return sources


def selected_attachment_sources(chunks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return text-free provenance for the exact attachment chunks sent to generation."""
    sources: list[dict[str, Any]] = []
    for source_order, chunk in enumerate(chunks):
        view = model_view_text(str(chunk.get("text") or ""))
        sources.append(
            {
                "evidence_ref": attachment_evidence_ref(chunk["chunk_id"]),
                "attachment_id": chunk["attachment_id"],
                "attachment_chunk_id": chunk["chunk_id"],
                "source_attachment_id": str(chunk["source_attachment_id"]),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "char_start": chunk.get("char_start"),
                "char_end": chunk.get("char_end"),
                "content_sha256": str(chunk["content_sha256"]),
                "source_order": source_order,
                "unicode_security_flags": list(view.flags),
            }
        )
    return sources


async def replace_summary_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    sources: Sequence[dict[str, Any]],
) -> None:
    """Atomically replace movement provenance for a persisted summary.

    Only identifiers and safe positional metadata are stored. Movement text,
    provider prompts, raw source payloads and party data never cross this table.
    """
    await conn.execute(
        "DELETE FROM process_summary_sources WHERE summary_id = $1",
        summary_id,
    )
    if not sources:
        return

    records: list[tuple[Any, ...]] = []
    for source in sources:
        step_id = source.get("step_id")
        if not isinstance(step_id, UUID):
            raise ValueError("summary source step_id must be a UUID")
        step_number = int(source["step_number"])
        source_order = int(source["source_order"])
        records.append(
            (
                summary_id,
                process_id,
                version_id,
                "movement",
                step_id,
                step_number,
                source.get("occurred_at"),
                source_order,
                str(source.get("source_text_sha256") or "").strip().lower(),
                _unicode_flags_json(source.get("unicode_security_flags")),
            )
        )

    await conn.executemany(
        """
        INSERT INTO process_summary_sources (
            summary_id, process_id, version_id, chunk_type,
            step_id, step_number, occurred_at, source_order,
            source_text_sha256, unicode_security_flags
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NULLIF($9, ''), $10::jsonb)
        """,
        records,
    )


async def replace_summary_attachment_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    sources: Sequence[dict[str, Any]],
) -> None:
    """Replace attachment provenance without storing attachment text."""
    await conn.execute(
        "DELETE FROM process_summary_attachment_sources WHERE summary_id = $1",
        summary_id,
    )
    if not sources:
        return

    records: list[tuple[Any, ...]] = []
    for source in sources:
        attachment_id = source.get("attachment_id")
        chunk_id = source.get("attachment_chunk_id")
        if not isinstance(attachment_id, UUID) or not isinstance(chunk_id, UUID):
            raise ValueError("attachment provenance ids must be UUIDs")
        source_attachment_id = str(source.get("source_attachment_id") or "").strip()
        digest = str(source.get("content_sha256") or "").strip().lower()
        if not source_attachment_id:
            raise ValueError("attachment provenance source_attachment_id is required")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid attachment provenance content_sha256")
        records.append(
            (
                summary_id,
                process_id,
                version_id,
                attachment_id,
                chunk_id,
                source_attachment_id,
                source.get("page_start"),
                source.get("page_end"),
                source.get("char_start"),
                source.get("char_end"),
                digest,
                int(source.get("source_order", 0)),
                _unicode_flags_json(source.get("unicode_security_flags")),
            )
        )

    await conn.executemany(
        """
        INSERT INTO process_summary_attachment_sources (
            summary_id, process_id, version_id, attachment_id, attachment_chunk_id,
            source_attachment_id, page_start, page_end, char_start, char_end,
            content_sha256, source_order, unicode_security_flags
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
        """,
        records,
    )


async def replace_summary_glossary_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    sources: Sequence[dict[str, Any]],
) -> None:
    """Replace the exact versioned TPU definitions used for one summary."""
    await conn.execute(
        "DELETE FROM process_summary_glossary_sources WHERE summary_id = $1",
        summary_id,
    )
    if not sources:
        return

    records: list[tuple[Any, ...]] = []
    for source in sources:
        kind = str(source.get("kind") or "").strip()
        code = str(source.get("code") or "").strip()
        tpu_version = str(source.get("tpu_version") or "").strip()
        publisher = str(source.get("publisher") or "").strip()
        origin = str(source.get("source") or "").strip()
        source_ref = str(source.get("source_ref") or "").strip()
        digest = str(source.get("definition_sha256") or "").strip()
        source_order = int(source.get("source_order", 0))
        if kind not in {"class", "subject"}:
            raise ValueError("invalid TPU glossary provenance kind")
        if not all((code, tpu_version, publisher, origin, source_ref)):
            raise ValueError("TPU glossary provenance is missing required metadata")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid TPU glossary definition_sha256")
        records.append(
            (
                summary_id,
                process_id,
                version_id,
                kind,
                code,
                tpu_version,
                publisher,
                origin,
                source_ref,
                digest,
                source_order,
            )
        )

    await conn.executemany(
        """
        INSERT INTO process_summary_glossary_sources (
            summary_id, process_id, version_id, kind, code, tpu_version,
            publisher, source, source_ref, definition_sha256, source_order
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        records,
    )


async def load_used_summary_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT pss.chunk_type,
               pss.step_id,
               pss.step_number,
               pss.occurred_at,
               pss.source_order,
               pss.source_text_sha256,
               pss.unicode_security_flags,
               ps.title,
               CASE
                   WHEN jsonb_typeof(ps.metadata->'source_step_number') = 'number'
                   THEN (ps.metadata->>'source_step_number')::bigint
                   ELSE NULL
               END AS source_step_number
        FROM process_summary_sources pss
        JOIN process_steps ps
          ON ps.id = pss.step_id
         AND ps.process_id = pss.process_id
         AND ps.version_id = pss.version_id
        WHERE pss.summary_id = $1
        ORDER BY pss.source_order
        """,
        summary_id,
    )
    sources: list[dict[str, Any]] = [
        {
            "kind": "movement",
            "chunk_type": str(row["chunk_type"]),
            "used_for_summary": True,
            "step_id": str(row["step_id"]),
            "step_number": int(row["step_number"]),
            "source_step_number": (
                int(row["source_step_number"])
                if row["source_step_number"] is not None
                else None
            ),
            "occurred_at": row["occurred_at"],
            "title": row["title"],
            "source_order": int(row["source_order"]),
            "source_text_sha256": (
                str(row["source_text_sha256"]) if row["source_text_sha256"] is not None else None
            ),
            "unicode_security_flags": list(row["unicode_security_flags"] or []),
        }
        for row in rows
    ]

    attachment_rows = await conn.fetch(
        """
        SELECT attachment_id, attachment_chunk_id, source_attachment_id,
               page_start, page_end, char_start, char_end,
               content_sha256, source_order, unicode_security_flags
        FROM process_summary_attachment_sources
        WHERE summary_id = $1
        ORDER BY source_order
        """,
        summary_id,
    )
    sources.extend(
        {
            "kind": "attachment",
            "used_for_summary": True,
            "attachment_id": str(row["attachment_id"]),
            "attachment_chunk_id": str(row["attachment_chunk_id"]),
            "source_attachment_id": str(row["source_attachment_id"]),
            "page_start": row["page_start"],
            "page_end": row["page_end"],
            "char_start": row["char_start"],
            "char_end": row["char_end"],
            "content_sha256": str(row["content_sha256"]),
            "source_order": int(row["source_order"]),
            "unicode_security_flags": list(row["unicode_security_flags"] or []),
        }
        for row in attachment_rows
    )

    glossary_rows = await conn.fetch(
        """
        SELECT kind, code, tpu_version, publisher, source, source_ref,
               definition_sha256, source_order
        FROM process_summary_glossary_sources
        WHERE summary_id = $1
        ORDER BY source_order
        """,
        summary_id,
    )
    sources.extend(
        {
            "kind": "tpu_glossary",
            "glossary_kind": str(row["kind"]),
            "code": str(row["code"]),
            "tpu_version": str(row["tpu_version"]),
            "publisher": str(row["publisher"]),
            "source": str(row["source"]),
            "source_ref": str(row["source_ref"]),
            "definition_sha256": str(row["definition_sha256"]),
            "used_for_summary": True,
            "source_order": int(row["source_order"]),
        }
        for row in glossary_rows
    )
    return sources
