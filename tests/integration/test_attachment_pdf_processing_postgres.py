from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from app.attachment_processing import AttachmentProcessingLimits, parse_pdf_attachment, process_attachment_bytes
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        TRUNCATE attachment_chunks, process_attachments, process_summary_glossary_sources,
                 process_summary_sources, process_summaries, process_steps,
                 tenant_processes, access_log, process_versions, processes, tenants
        RESTART IDENTITY CASCADE
        """
    )


async def _fixture(conn: asyncpg.Connection):
    process_id = uuid4()
    version_id = uuid4()
    await conn.execute(
        "INSERT INTO processes (id, code, secrecy_level) VALUES ($1, $2, 0)",
        process_id,
        "0000000-00.2026.8.21.1378",
    )
    await conn.execute(
        """
        INSERT INTO process_versions (id, process_id, source_request_id, finalized)
        VALUES ($1, $2, 'req-attachment-pdf', TRUE)
        """,
        version_id,
        process_id,
    )
    await conn.execute(
        "UPDATE processes SET current_version_id=$2 WHERE id=$1",
        process_id,
        version_id,
    )
    return process_id, version_id


@pytest.mark.asyncio
async def test_pdf_processing_persists_page_aware_ready_chunks(monkeypatch) -> None:
    from app import attachment_processing as processing

    async def fake_sandbox(data, *, content_type, max_bytes, chunk_chars):
        return parse_pdf_attachment(
            data,
            content_type=content_type,
            limits=AttachmentProcessingLimits(max_bytes=max_bytes, chunk_chars=chunk_chars),
        )

    monkeypatch.setattr(processing, "parse_attachment_sandboxed", fake_sandbox)
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _fixture(conn)
        result = await process_attachment_bytes(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-pdf-local",
            content_type="application/pdf",
            data=_text_pdf("conteudo sintetico da pagina"),
            limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
        )
        assert result["status"] == "ready"
        assert result["chunk_count"] == 1
        row = await conn.fetchrow(
            """
            SELECT pa.status, pa.content_type, ac.page_start, ac.page_end,
                   ac.char_start, ac.char_end, ac.text
            FROM process_attachments pa
            JOIN attachment_chunks ac ON ac.attachment_id=pa.id
            WHERE pa.id=$1
            """,
            result["attachment_id"],
        )
        assert row["status"] == "ready"
        assert row["content_type"] == "application/pdf"
        assert row["page_start"] == row["page_end"] == 1
        assert row["char_start"] == 0
        assert row["char_end"] == len(row["text"])
        assert "conteudo sintetico da pagina" in row["text"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_invalid_pdf_is_attachment_local_corrupt_state(monkeypatch) -> None:
    from app import attachment_processing as processing

    async def fake_sandbox(data, *, content_type, max_bytes, chunk_chars):
        return parse_pdf_attachment(
            data,
            content_type=content_type,
            limits=AttachmentProcessingLimits(max_bytes=max_bytes, chunk_chars=chunk_chars),
        )

    monkeypatch.setattr(processing, "parse_attachment_sandboxed", fake_sandbox)
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _fixture(conn)
        result = await process_attachment_bytes(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id="att-pdf-corrupt",
            content_type="application/pdf",
            data=b"not a pdf",
            limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
        )
        assert result["status"] == "corrupt"
        assert result["error_code"] == "invalid_pdf_header"
        assert result["chunk_count"] == 0
        assert await conn.fetchval("SELECT count(*) FROM process_steps") == 0
    finally:
        await conn.close()
