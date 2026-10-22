from __future__ import annotations

import asyncio
import hashlib
import io
import math
import os
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import asyncpg
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from app.attachment_sandbox import parse_attachment_sandboxed
from app.attachments import AttachmentChunkInput, replace_attachment_chunks, upsert_attachment_state

DEFAULT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_ATTACHMENT_CHUNK_CHARS = 4_000
MIN_ATTACHMENT_CHUNK_CHARS = 256
DEFAULT_ATTACHMENT_OCR_TIMEOUT_SECONDS = 30
DEFAULT_ATTACHMENT_OCR_LANGUAGE = "por"
DEFAULT_ATTACHMENT_PDF_OCR_SCALE = 2.0
DEFAULT_ATTACHMENT_PDF_OCR_MAX_PAGES = 100
OCR_IMAGE_CONTENT_TYPES = ("image/png", "image/jpeg")

FailureStatus = Literal["corrupt", "unreadable"]


@dataclass(frozen=True, slots=True)
class AttachmentProcessingLimits:
    max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES
    chunk_chars: int = DEFAULT_ATTACHMENT_CHUNK_CHARS


class AttachmentProcessingError(ValueError):
    def __init__(self, *, status: FailureStatus, error_code: str):
        super().__init__(error_code)
        self.status = status
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class AttachmentOCRConfig:
    enabled: bool = False
    binary: str = "tesseract"
    language: str = DEFAULT_ATTACHMENT_OCR_LANGUAGE
    timeout_seconds: int = DEFAULT_ATTACHMENT_OCR_TIMEOUT_SECONDS
    pdf_scale: float = DEFAULT_ATTACHMENT_PDF_OCR_SCALE
    pdf_max_pages: int = DEFAULT_ATTACHMENT_PDF_OCR_MAX_PAGES


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _positive_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a finite number greater than zero")
    return value


def attachment_ocr_config() -> AttachmentOCRConfig:
    enabled = _env_bool("ATTACHMENT_OCR_ENABLED", False)
    binary = str(os.getenv("ATTACHMENT_OCR_BINARY") or "tesseract").strip()
    language = str(
        os.getenv("ATTACHMENT_OCR_LANGUAGE") or DEFAULT_ATTACHMENT_OCR_LANGUAGE
    ).strip()
    timeout_seconds = _positive_env_int(
        "ATTACHMENT_OCR_TIMEOUT_SECONDS",
        DEFAULT_ATTACHMENT_OCR_TIMEOUT_SECONDS,
    )
    pdf_scale = _positive_env_float(
        "ATTACHMENT_PDF_OCR_SCALE",
        DEFAULT_ATTACHMENT_PDF_OCR_SCALE,
    )
    pdf_max_pages = _positive_env_int(
        "ATTACHMENT_PDF_OCR_MAX_PAGES",
        DEFAULT_ATTACHMENT_PDF_OCR_MAX_PAGES,
    )
    if enabled and not binary:
        raise RuntimeError("ATTACHMENT_OCR_BINARY is required when OCR is enabled")
    if enabled and not language:
        raise RuntimeError("ATTACHMENT_OCR_LANGUAGE is required when OCR is enabled")
    return AttachmentOCRConfig(
        enabled=enabled,
        binary=binary,
        language=language,
        timeout_seconds=timeout_seconds,
        pdf_scale=pdf_scale,
        pdf_max_pages=pdf_max_pages,
    )


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


def attachment_processing_limits() -> AttachmentProcessingLimits:
    max_bytes = _positive_env_int("ATTACHMENT_MAX_BYTES", DEFAULT_ATTACHMENT_MAX_BYTES)
    chunk_chars = _positive_env_int("ATTACHMENT_CHUNK_CHARS", DEFAULT_ATTACHMENT_CHUNK_CHARS)
    if chunk_chars < MIN_ATTACHMENT_CHUNK_CHARS:
        raise RuntimeError(
            f"ATTACHMENT_CHUNK_CHARS must be at least {MIN_ATTACHMENT_CHUNK_CHARS}"
        )
    return AttachmentProcessingLimits(max_bytes=max_bytes, chunk_chars=chunk_chars)


def normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _validate_common_payload(
    data: bytes,
    *,
    content_type: str,
    expected_content_type: str,
    limits: AttachmentProcessingLimits,
) -> None:
    normalized_type = normalize_content_type(content_type)
    if normalized_type != expected_content_type:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="unsupported_content_type",
        )
    if len(data) > limits.max_bytes:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="attachment_too_large",
        )
    if not data:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="empty_attachment",
        )


def _validate_text_payload(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits,
) -> str:
    _validate_common_payload(
        data,
        content_type=content_type,
        expected_content_type="text/plain",
        limits=limits,
    )
    if b"\x00" in data:
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_text_bytes",
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_utf8",
        ) from exc

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="empty_text",
        )
    return normalized


def chunk_attachment_text(text: str, *, chunk_chars: int) -> list[AttachmentChunkInput]:
    if chunk_chars < MIN_ATTACHMENT_CHUNK_CHARS:
        raise ValueError(f"chunk_chars must be at least {MIN_ATTACHMENT_CHUNK_CHARS}")
    if not text:
        return []

    chunks: list[AttachmentChunkInput] = []
    cursor = 0
    text_length = len(text)
    while cursor < text_length:
        hard_end = min(cursor + chunk_chars, text_length)
        end = hard_end
        if hard_end < text_length:
            newline = text.rfind("\n", cursor, hard_end)
            space = text.rfind(" ", cursor, hard_end)
            preferred = max(newline, space)
            if preferred > cursor + (chunk_chars // 2):
                end = preferred

        while end > cursor and text[end - 1].isspace():
            end -= 1
        if end <= cursor:
            end = hard_end

        chunk_text = text[cursor:end]
        chunks.append(
            AttachmentChunkInput(
                text=chunk_text,
                char_start=cursor,
                char_end=end,
            )
        )
        cursor = end
        while cursor < text_length and text[cursor].isspace():
            cursor += 1

    return chunks


def parse_text_attachment(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits | None = None,
) -> list[AttachmentChunkInput]:
    effective_limits = limits or attachment_processing_limits()
    text = _validate_text_payload(
        data,
        content_type=content_type,
        limits=effective_limits,
    )
    return chunk_attachment_text(text, chunk_chars=effective_limits.chunk_chars)


def parse_pdf_attachment(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits | None = None,
) -> list[AttachmentChunkInput]:
    """Extract text-layer PDF pages locally; image-only PDFs remain unreadable for OCR."""
    effective_limits = limits or attachment_processing_limits()
    _validate_common_payload(
        data,
        content_type=content_type,
        expected_content_type="application/pdf",
        limits=effective_limits,
    )
    if not data.lstrip().startswith(b"%PDF-"):
        raise AttachmentProcessingError(status="corrupt", error_code="invalid_pdf_header")

    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise AttachmentProcessingError(status="unreadable", error_code="encrypted_pdf")

        chunks: list[AttachmentChunkInput] = []
        for page_number, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            normalized = extracted.replace("\r\n", "\n").replace("\r", "\n").strip()
            if not normalized:
                continue
            page_chunks = chunk_attachment_text(
                normalized,
                chunk_chars=effective_limits.chunk_chars,
            )
            for chunk in page_chunks:
                chunks.append(
                    AttachmentChunkInput(
                        text=chunk.text,
                        page_start=page_number,
                        page_end=page_number,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                    )
                )
    except AttachmentProcessingError:
        raise
    except FileNotDecryptedError as exc:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="encrypted_pdf",
        ) from exc
    except (PdfReadError, OSError, ValueError, TypeError) as exc:
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_pdf",
        ) from exc

    if not chunks:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="pdf_text_unavailable",
        )
    return chunks


def _validate_image_payload(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits,
) -> str:
    normalized_type = normalize_content_type(content_type)
    if normalized_type not in OCR_IMAGE_CONTENT_TYPES:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="unsupported_content_type",
        )
    if len(data) > limits.max_bytes:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="attachment_too_large",
        )
    if not data:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="empty_attachment",
        )
    if normalized_type == "image/png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AttachmentProcessingError(
                status="corrupt",
                error_code="invalid_image_header",
            )
        return ".png"
    if not data.startswith(b"\xff\xd8\xff"):
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_image_header",
        )
    return ".jpg"


def _run_tesseract_ocr(
    data: bytes,
    *,
    suffix: str,
    config: AttachmentOCRConfig,
) -> str:
    with tempfile.TemporaryDirectory(prefix="rpy-ocr-") as directory:
        image_path = Path(directory) / f"attachment{suffix}"
        image_path.write_bytes(data)
        try:
            completed = subprocess.run(
                [
                    config.binary,
                    str(image_path),
                    "stdout",
                    "-l",
                    config.language,
                    "--psm",
                    "6",
                    "quiet",
                ],
                check=False,
                capture_output=True,
                timeout=config.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise AttachmentProcessingError(
                status="unreadable",
                error_code="ocr_unavailable",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AttachmentProcessingError(
                status="unreadable",
                error_code="ocr_timeout",
            ) from exc
        if completed.returncode != 0:
            raise AttachmentProcessingError(
                status="unreadable",
                error_code="ocr_failed",
            )
        try:
            text = completed.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentProcessingError(
                status="unreadable",
                error_code="ocr_invalid_utf8",
            ) from exc
        return text


async def parse_image_attachment_ocr(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits | None = None,
    config: AttachmentOCRConfig | None = None,
) -> list[AttachmentChunkInput]:
    effective_limits = limits or attachment_processing_limits()
    effective_config = config or attachment_ocr_config()
    suffix = _validate_image_payload(
        data,
        content_type=content_type,
        limits=effective_limits,
    )
    if not effective_config.enabled:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="ocr_disabled",
        )
    text = await asyncio.to_thread(
        _run_tesseract_ocr,
        data,
        suffix=suffix,
        config=effective_config,
    )
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="ocr_no_text",
        )
    return chunk_attachment_text(
        normalized,
        chunk_chars=effective_limits.chunk_chars,
    )


def _render_pdf_pages_for_ocr(
    data: bytes,
    *,
    scale: float,
    max_pages: int,
) -> list[tuple[int, bytes]]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="pdf_ocr_rasterizer_unavailable",
        ) from exc

    document = None
    try:
        document = pdfium.PdfDocument(data)
        page_count = len(document)
        if page_count > max_pages:
            raise AttachmentProcessingError(
                status="unreadable",
                error_code="pdf_ocr_too_many_pages",
            )
        rendered: list[tuple[int, bytes]] = []
        for page_index in range(page_count):
            page = document[page_index]
            bitmap = None
            image = None
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                rendered.append((page_index + 1, buffer.getvalue()))
            finally:
                if image is not None and hasattr(image, "close"):
                    image.close()
                if bitmap is not None and hasattr(bitmap, "close"):
                    bitmap.close()
                if hasattr(page, "close"):
                    page.close()
        return rendered
    except AttachmentProcessingError:
        raise
    except Exception as exc:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="pdf_ocr_rasterize_failed",
        ) from exc
    finally:
        if document is not None and hasattr(document, "close"):
            document.close()


async def parse_pdf_attachment_ocr(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits | None = None,
    config: AttachmentOCRConfig | None = None,
) -> list[AttachmentChunkInput]:
    effective_limits = limits or attachment_processing_limits()
    effective_config = config or attachment_ocr_config()
    _validate_common_payload(
        data,
        content_type=content_type,
        expected_content_type="application/pdf",
        limits=effective_limits,
    )
    if not data.lstrip().startswith(b"%PDF-"):
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_pdf_header",
        )
    if not effective_config.enabled:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="ocr_disabled",
        )

    pages = await asyncio.to_thread(
        _render_pdf_pages_for_ocr,
        data,
        scale=effective_config.pdf_scale,
        max_pages=effective_config.pdf_max_pages,
    )
    chunks: list[AttachmentChunkInput] = []
    for page_number, png_bytes in pages:
        text = await asyncio.to_thread(
            _run_tesseract_ocr,
            png_bytes,
            suffix=".png",
            config=effective_config,
        )
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            continue
        for chunk in chunk_attachment_text(
            normalized,
            chunk_chars=effective_limits.chunk_chars,
        ):
            chunks.append(
                AttachmentChunkInput(
                    text=chunk.text,
                    page_start=page_number,
                    page_end=page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
    if not chunks:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="ocr_no_text",
        )
    return chunks


def parse_attachment(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits | None = None,
) -> list[AttachmentChunkInput]:
    normalized_type = normalize_content_type(content_type)
    if normalized_type == "text/plain":
        return parse_text_attachment(data, content_type=content_type, limits=limits)
    raise AttachmentProcessingError(
        status="unreadable",
        error_code="unsupported_content_type",
    )


async def process_attachment_bytes(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    source_attachment_id: str,
    content_type: str,
    data: bytes,
    limits: AttachmentProcessingLimits | None = None,
) -> dict[str, object]:
    """Parse/persist already-authorized bytes without retaining or logging the raw document."""
    effective_limits = limits or attachment_processing_limits()
    digest = hashlib.sha256(data).hexdigest()
    try:
        normalized_type = normalize_content_type(content_type)
        if normalized_type in OCR_IMAGE_CONTENT_TYPES or normalized_type == "application/pdf":
            chunks = await parse_attachment_sandboxed(
                data,
                content_type=content_type,
                max_bytes=effective_limits.max_bytes,
                chunk_chars=effective_limits.chunk_chars,
            )
        else:
            chunks = parse_attachment(
                data,
                content_type=content_type,
                limits=effective_limits,
            )
    except AttachmentProcessingError as exc:
        attachment_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id=source_attachment_id,
            status=exc.status,
            content_type=normalize_content_type(content_type),
            byte_size=len(data),
            content_sha256=digest,
            error_code=exc.error_code,
        )
        return {
            "attachment_id": attachment_id,
            "status": exc.status,
            "error_code": exc.error_code,
            "chunk_count": 0,
        }

    attachment_id = await upsert_attachment_state(
        conn,
        process_id=process_id,
        version_id=version_id,
        source_attachment_id=source_attachment_id,
        status="pending",
        content_type=normalize_content_type(content_type),
        byte_size=len(data),
        content_sha256=digest,
    )
    chunk_count = await replace_attachment_chunks(
        conn,
        attachment_id=attachment_id,
        process_id=process_id,
        version_id=version_id,
        chunks=chunks,
    )
    return {
        "attachment_id": attachment_id,
        "status": "ready",
        "error_code": None,
        "chunk_count": chunk_count,
    }


async def process_text_attachment_bytes(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    source_attachment_id: str,
    content_type: str,
    data: bytes,
    limits: AttachmentProcessingLimits | None = None,
) -> dict[str, object]:
    """Backward-compatible text-only entry point for the Phase 2 processing block."""
    if normalize_content_type(content_type) != "text/plain":
        effective_limits = limits or attachment_processing_limits()
        digest = hashlib.sha256(data).hexdigest()
        attachment_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id=source_attachment_id,
            status="unreadable",
            content_type=normalize_content_type(content_type),
            byte_size=len(data),
            content_sha256=digest,
            error_code="unsupported_content_type",
        )
        return {
            "attachment_id": attachment_id,
            "status": "unreadable",
            "error_code": "unsupported_content_type",
            "chunk_count": 0,
        }
    return await process_attachment_bytes(
        conn,
        process_id=process_id,
        version_id=version_id,
        source_attachment_id=source_attachment_id,
        content_type=content_type,
        data=data,
        limits=limits,
    )
