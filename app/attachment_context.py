from __future__ import annotations

import os
import re
from typing import Any
from uuid import UUID

import asyncpg

from app.attachments import search_authorized_attachment_chunks
from app.claim_evidence import attachment_evidence_ref
from app.provenance import selected_attachment_sources
from app.unicode_security import model_view_text

DEFAULT_ATTACHMENT_RETRIEVAL_LIMIT = 12
DEFAULT_PROVIDER_ATTACHMENT_TEXT_MAX_CHARS = 20_000
ATTACHMENT_TRUNCATION_MARKER = "… [truncated]"


def _positive_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def attachment_context_limits() -> tuple[int, int]:
    return (
        _positive_env_int("ATTACHMENT_RETRIEVAL_LIMIT", DEFAULT_ATTACHMENT_RETRIEVAL_LIMIT),
        _positive_env_int(
            "PROVIDER_ATTACHMENT_TEXT_MAX_CHARS",
            DEFAULT_PROVIDER_ATTACHMENT_TEXT_MAX_CHARS,
        ),
    )


def attachment_retrieval_query(process_context: dict[str, Any], base_query: str) -> str:
    """Expand legal retrieval hints without turning recall terms into an AND filter."""
    values = [base_query]
    class_name = str(process_context.get("class_name") or "").strip()
    if class_name:
        values.append(class_name)
    for subject in process_context.get("subjects", []):
        if not isinstance(subject, dict):
            continue
        for key in ("name", "code"):
            value = str(subject.get(key) or "").strip()
            if value:
                values.append(value)

    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        for term in re.findall(r"\w+", value, flags=re.UNICODE):
            normalized = term.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            terms.append(term)
    return " OR ".join(terms)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(ATTACHMENT_TRUNCATION_MARKER):
        return text[:limit]
    return text[: limit - len(ATTACHMENT_TRUNCATION_MARKER)] + ATTACHMENT_TRUNCATION_MARKER


def serialize_attachment_chunks(
    chunks: list[dict[str, Any]], *, total_text_limit: int
) -> list[dict[str, Any]]:
    if total_text_limit <= 0:
        raise ValueError("total_text_limit must be greater than zero")
    rendered: list[dict[str, Any]] = []
    remaining = total_text_limit
    remaining_items = len(chunks)
    for chunk in chunks:
        text = model_view_text(str(chunk.get("text") or "")).text
        allowance = remaining // remaining_items if remaining_items else 0
        bounded = _truncate(text, allowance)
        rendered.append(
            {
                "evidence_ref": attachment_evidence_ref(chunk["chunk_id"]),
                "source_attachment_id": str(chunk["source_attachment_id"]),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "char_start": chunk.get("char_start"),
                "char_end": chunk.get("char_end"),
                "text": bounded,
            }
        )
        remaining -= len(bounded)
        remaining_items -= 1
    return rendered


async def resolve_generation_tenant(
    pool: asyncpg.Pool,
    *,
    judit_request_id: str | None,
    process_id: UUID,
) -> UUID | None:
    if not judit_request_id:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT tjr.tenant_id
            FROM tenant_judit_requests tjr
            JOIN tenant_processes tp
              ON tp.tenant_id=tjr.tenant_id
             AND tp.process_id=$2
            WHERE tjr.judit_request_id=$1
            """,
            judit_request_id,
            process_id,
        )


async def load_attachment_context(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    process_id: UUID,
    version_id: UUID,
    process_context: dict[str, Any],
    base_query: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    retrieval_limit, text_limit = attachment_context_limits()
    query = attachment_retrieval_query(process_context, base_query)
    async with pool.acquire() as conn:
        chunks = await search_authorized_attachment_chunks(
            conn,
            tenant_id=tenant_id,
            process_id=process_id,
            version_id=version_id,
            query=query,
            limit=retrieval_limit,
        )
        status_rows = await conn.fetch(
            """
            SELECT pa.status, count(*)::int AS count
            FROM tenant_processes tp
            JOIN processes p
              ON p.id=tp.process_id
             AND p.id=$2
             AND p.current_version_id=$3
             AND p.secrecy_level=0
            JOIN process_attachments pa
              ON pa.process_id=p.id
             AND pa.version_id=$3
            WHERE tp.tenant_id=$1
            GROUP BY pa.status
            """,
            tenant_id,
            process_id,
            version_id,
        )
    statuses = {str(row["status"]): int(row["count"]) for row in status_rows}
    return (
        serialize_attachment_chunks(chunks, total_text_limit=text_limit),
        selected_attachment_sources(chunks),
        statuses,
    )
