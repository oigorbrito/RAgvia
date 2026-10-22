from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import struct
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.attachments import AttachmentChunkInput
from app.json_utils import loads_strict_json

DEFAULT_SOCKET_PATH = "/run/rpy-parser/parser.sock"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_SERVER_REQUEST_TIMEOUT_SECONDS = 45.0
MAX_FRAME_BYTES = 20 * 1024 * 1024
PROTOCOL_VERSION = 1


def parser_socket_path() -> str:
    return str(os.getenv("ATTACHMENT_PARSER_SOCKET") or DEFAULT_SOCKET_PATH).strip()


def parser_timeout_seconds() -> float:
    raw = str(os.getenv("ATTACHMENT_PARSER_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("ATTACHMENT_PARSER_TIMEOUT_SECONDS must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(
            "ATTACHMENT_PARSER_TIMEOUT_SECONDS must be a finite number greater than zero"
        )
    return value


def server_request_timeout_seconds() -> float:
    raw = str(
        os.getenv("ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS")
        or DEFAULT_SERVER_REQUEST_TIMEOUT_SECONDS
    )
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS must be numeric"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(
            "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS must be a finite number greater than zero"
        )
    return value


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError("attachment parser request exceeds frame limit")
    return struct.pack("!I", len(body)) + body


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(4)
    (size,) = struct.unpack("!I", header)
    if size <= 0 or size > MAX_FRAME_BYTES:
        raise ValueError("invalid attachment parser frame size")
    body = await reader.readexactly(size)
    payload = loads_strict_json(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("attachment parser frame must contain an object")
    return payload


def _chunk_from_payload(item: dict[str, Any]) -> AttachmentChunkInput:
    return AttachmentChunkInput(
        text=str(item["text"]),
        page_start=item.get("page_start"),
        page_end=item.get("page_end"),
        char_start=item.get("char_start"),
        char_end=item.get("char_end"),
    )


async def parse_attachment_sandboxed(
    data: bytes,
    *,
    content_type: str,
    max_bytes: int,
    chunk_chars: int,
    socket_path: str | None = None,
    timeout_seconds: float | None = None,
) -> list[AttachmentChunkInput]:
    from app.attachment_processing import AttachmentProcessingError

    path = socket_path or parser_socket_path()
    timeout = parser_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("attachment parser timeout_seconds must be finite and greater than zero")
    request = {
        "version": PROTOCOL_VERSION,
        "content_type": content_type,
        "max_bytes": max_bytes,
        "chunk_chars": chunk_chars,
        "data_b64": base64.b64encode(data).decode("ascii"),
    }

    async def _exchange() -> dict[str, Any]:
        try:
            reader, writer = await asyncio.open_unix_connection(path)
        except (FileNotFoundError, ConnectionError, OSError) as exc:
            raise AttachmentProcessingError(
                status="unreadable", error_code="parser_unavailable"
            ) from exc
        try:
            writer.write(_frame(request))
            await writer.drain()
            return await _read_frame(reader)
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        response = await asyncio.wait_for(_exchange(), timeout=timeout)
    except AttachmentProcessingError:
        raise
    except TimeoutError as exc:
        raise AttachmentProcessingError(
            status="unreadable", error_code="parser_timeout"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, asyncio.IncompleteReadError) as exc:
        raise AttachmentProcessingError(
            status="unreadable", error_code="parser_protocol_error"
        ) from exc

    if response.get("version") != PROTOCOL_VERSION:
        raise AttachmentProcessingError(
            status="unreadable", error_code="parser_protocol_error"
        )
    if response.get("ok") is not True:
        status = str(response.get("status") or "unreadable")
        error_code = str(response.get("error_code") or "parser_failed")
        if status not in {"corrupt", "unreadable"}:
            status = "unreadable"
        raise AttachmentProcessingError(status=status, error_code=error_code)

    raw_chunks = response.get("chunks")
    if not isinstance(raw_chunks, list):
        raise AttachmentProcessingError(
            status="unreadable", error_code="parser_protocol_error"
        )
    try:
        return [_chunk_from_payload(dict(item)) for item in raw_chunks]
    except (KeyError, TypeError, ValueError) as exc:
        raise AttachmentProcessingError(
            status="unreadable", error_code="parser_protocol_error"
        ) from exc


async def _parse_request(request: dict[str, Any]) -> dict[str, Any]:
    from app.attachment_processing import (
        AttachmentOCRConfig,
        AttachmentProcessingError,
        AttachmentProcessingLimits,
        OCR_IMAGE_CONTENT_TYPES,
        attachment_ocr_config,
        attachment_processing_limits,
        normalize_content_type,
        parse_image_attachment_ocr,
        parse_pdf_attachment,
        parse_pdf_attachment_ocr,
    )

    if request.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported parser protocol version")
    content_type = str(request.get("content_type") or "")
    data_b64 = request.get("data_b64")
    if not isinstance(data_b64, str):
        raise ValueError("missing parser payload")
    data = base64.b64decode(data_b64.encode("ascii"), validate=True)
    raw_max_bytes = request.get("max_bytes")
    raw_chunk_chars = request.get("chunk_chars")
    try:
        requested_max_bytes = int(raw_max_bytes)
        requested_chunk_chars = int(raw_chunk_chars)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid parser resource limits") from exc
    if requested_max_bytes <= 0 or requested_chunk_chars <= 0:
        raise ValueError("parser resource limits must be positive")
    configured_limits = attachment_processing_limits()
    limits = AttachmentProcessingLimits(
        max_bytes=min(requested_max_bytes, configured_limits.max_bytes),
        chunk_chars=min(requested_chunk_chars, configured_limits.chunk_chars),
    )
    normalized_type = normalize_content_type(content_type)

    try:
        if normalized_type in OCR_IMAGE_CONTENT_TYPES:
            chunks = await parse_image_attachment_ocr(
                data,
                content_type=content_type,
                limits=limits,
                config=attachment_ocr_config(),
            )
        elif normalized_type == "application/pdf":
            try:
                chunks = parse_pdf_attachment(
                    data, content_type=content_type, limits=limits
                )
            except AttachmentProcessingError as exc:
                if exc.error_code != "pdf_text_unavailable":
                    raise
                ocr_config: AttachmentOCRConfig = attachment_ocr_config()
                if not ocr_config.enabled:
                    raise
                chunks = await parse_pdf_attachment_ocr(
                    data,
                    content_type=content_type,
                    limits=limits,
                    config=ocr_config,
                )
        else:
            raise AttachmentProcessingError(
                status="unreadable", error_code="unsupported_content_type"
            )
    except AttachmentProcessingError as exc:
        return {
            "version": PROTOCOL_VERSION,
            "ok": False,
            "status": exc.status,
            "error_code": exc.error_code,
        }

    return {
        "version": PROTOCOL_VERSION,
        "ok": True,
        "chunks": [asdict(chunk) for chunk in chunks],
    }


async def _handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    async def _serve_request() -> dict[str, Any]:
        request = await _read_frame(reader)
        return await _parse_request(request)

    try:
        response = await asyncio.wait_for(
            _serve_request(), timeout=server_request_timeout_seconds()
        )
    except TimeoutError:
        response = {
            "version": PROTOCOL_VERSION,
            "ok": False,
            "status": "unreadable",
            "error_code": "parser_timeout",
        }
    except Exception:
        response = {
            "version": PROTOCOL_VERSION,
            "ok": False,
            "status": "unreadable",
            "error_code": "parser_protocol_error",
        }
    try:
        writer.write(_frame(response))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def serve() -> None:
    socket_path = Path(parser_socket_path())
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    async with server:
        await server.serve_forever()


def main() -> int:
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
