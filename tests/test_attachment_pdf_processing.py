from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from app.attachment_processing import (
    AttachmentOCRConfig,
    AttachmentProcessingError,
    AttachmentProcessingLimits,
    parse_attachment,
    parse_pdf_attachment,
    parse_pdf_attachment_ocr,
    process_attachment_bytes,
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


def _blank_pdf(*, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("synthetic-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_text_layer_is_extracted_with_page_positions():
    chunks = parse_pdf_attachment(
        _text_pdf("Texto sintetico do PDF"),
        content_type="application/pdf",
        limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
    )
    assert len(chunks) == 1
    assert "Texto sintetico do PDF" in chunks[0].text
    assert chunks[0].page_start == chunks[0].page_end == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(chunks[0].text)


def test_worker_dispatcher_keeps_binary_formats_outside_process():
    limits = AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256)
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_attachment(
            _text_pdf("PDF"), content_type="application/pdf", limits=limits
        )
    assert exc_info.value.error_code == "unsupported_content_type"
    assert parse_attachment(
        b"texto", content_type="text/plain; charset=utf-8", limits=limits
    )[0].text == "texto"


@pytest.mark.parametrize(
    ("data", "error_code", "status"),
    [
        (b"not a pdf", "invalid_pdf_header", "corrupt"),
        (_blank_pdf(), "pdf_text_unavailable", "unreadable"),
        (_blank_pdf(encrypted=True), "encrypted_pdf", "unreadable"),
    ],
)
def test_pdf_failures_are_classified(data, error_code, status):
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_pdf_attachment(
            data,
            content_type="application/pdf",
            limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
        )
    assert exc_info.value.error_code == error_code
    assert exc_info.value.status == status


def test_pdf_size_limit_is_checked_before_parse():
    data = _text_pdf("texto")
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_pdf_attachment(
            data,
            content_type="application/pdf",
            limits=AttachmentProcessingLimits(max_bytes=10, chunk_chars=256),
        )
    assert exc_info.value.error_code == "attachment_too_large"
    assert exc_info.value.status == "unreadable"



@pytest.mark.asyncio
async def test_textless_pdf_can_be_rasterized_for_local_ocr(monkeypatch):
    from app import attachment_processing as processing

    observed = []

    def fake_ocr(data, *, suffix, config):
        observed.append((suffix, data[:8], config.language))
        return "Texto reconhecido da página"

    monkeypatch.setattr(processing, "_run_tesseract_ocr", fake_ocr)
    chunks = await parse_pdf_attachment_ocr(
        _blank_pdf(),
        content_type="application/pdf",
        limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
        config=AttachmentOCRConfig(
            enabled=True,
            language="por",
            pdf_scale=1.0,
            pdf_max_pages=10,
        ),
    )

    assert len(chunks) == 1
    assert chunks[0].text == "Texto reconhecido da página"
    assert chunks[0].page_start == chunks[0].page_end == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(chunks[0].text)
    assert observed and observed[0][0] == ".png"
    assert observed[0][1] == b"\x89PNG\r\n\x1a\n"
    assert observed[0][2] == "por"


@pytest.mark.asyncio
async def test_pdf_ocr_page_limit_is_enforced_before_tesseract(monkeypatch):
    from app import attachment_processing as processing

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)

    monkeypatch.setattr(
        processing,
        "_run_tesseract_ocr",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_pdf_attachment_ocr(
            buffer.getvalue(),
            content_type="application/pdf",
            limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
            config=AttachmentOCRConfig(
                enabled=True,
                pdf_scale=1.0,
                pdf_max_pages=1,
            ),
        )

    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "pdf_ocr_too_many_pages"


@pytest.mark.asyncio
async def test_pdf_ocr_empty_pages_are_unreadable(monkeypatch):
    from app import attachment_processing as processing

    monkeypatch.setattr(
        processing,
        "_run_tesseract_ocr",
        lambda data, *, suffix, config: "  \n ",
    )
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_pdf_attachment_ocr(
            _blank_pdf(),
            content_type="application/pdf",
            limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
            config=AttachmentOCRConfig(enabled=True, pdf_scale=1.0),
        )

    assert exc_info.value.error_code == "ocr_no_text"



@pytest.mark.asyncio
async def test_textless_pdf_preserves_attachment_local_status_from_sandbox(monkeypatch):
    from app import attachment_processing as processing

    async def fake_parse(*args, **kwargs):
        raise AttachmentProcessingError(
            status="unreadable", error_code="pdf_text_unavailable"
        )

    monkeypatch.setattr(processing, "parse_attachment_sandboxed", fake_parse)

    async def fake_upsert(conn, **kwargs):
        assert kwargs["status"] == "unreadable"
        assert kwargs["error_code"] == "pdf_text_unavailable"
        return "synthetic-attachment-id"

    monkeypatch.setattr(processing, "upsert_attachment_state", fake_upsert)
    result = await process_attachment_bytes(
        object(),
        process_id=__import__("uuid").uuid4(),
        version_id=__import__("uuid").uuid4(),
        source_attachment_id="pdf-no-text",
        content_type="application/pdf",
        data=_blank_pdf(),
        limits=AttachmentProcessingLimits(max_bytes=50_000, chunk_chars=256),
    )

    assert result == {
        "attachment_id": "synthetic-attachment-id",
        "status": "unreadable",
        "error_code": "pdf_text_unavailable",
        "chunk_count": 0,
    }
