from __future__ import annotations

import asyncio
import pytest

from app.attachment_processing import AttachmentProcessingError, AttachmentProcessingLimits
from app.attachment_sandbox import (
    _frame,
    _handle_client,
    _parse_request,
    parse_attachment_sandboxed,
    parser_timeout_seconds,
    server_request_timeout_seconds,
)


@pytest.mark.asyncio
async def test_sandbox_classifies_corrupt_pdf_without_crashing_server() -> None:
    import base64

    response = await _parse_request(
        {
            "version": 1,
            "content_type": "application/pdf",
            "max_bytes": 50_000,
            "chunk_chars": 256,
            "data_b64": base64.b64encode(b"not a pdf").decode("ascii"),
        }
    )
    assert response == {
        "version": 1,
        "ok": False,
        "status": "corrupt",
        "error_code": "invalid_pdf_header",
    }


@pytest.mark.asyncio
async def test_sandbox_client_round_trips_over_unix_socket(tmp_path) -> None:
    socket_path = tmp_path / "parser.sock"
    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    try:
        with pytest.raises(AttachmentProcessingError) as exc_info:
            await parse_attachment_sandboxed(
                b"not a pdf",
                content_type="application/pdf",
                max_bytes=50_000,
                chunk_chars=256,
                socket_path=str(socket_path),
                timeout_seconds=1,
            )
        assert exc_info.value.status == "corrupt"
        assert exc_info.value.error_code == "invalid_pdf_header"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_sandbox_timeout_is_attachment_local(monkeypatch) -> None:
    async def blocked_open(*args, **kwargs):
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "open_unix_connection", blocked_open)
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_attachment_sandboxed(
            b"%PDF-1.4\n",
            content_type="application/pdf",
            max_bytes=50_000,
            chunk_chars=256,
            socket_path="/run/rpy-parser/parser.sock",
            timeout_seconds=0.01,
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "parser_timeout"


@pytest.mark.asyncio
async def test_sandbox_missing_socket_is_attachment_local(tmp_path) -> None:
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_attachment_sandboxed(
            b"%PDF-1.4\n",
            content_type="application/pdf",
            max_bytes=50_000,
            chunk_chars=256,
            socket_path=str(tmp_path / "missing.sock"),
            timeout_seconds=1,
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "parser_unavailable"



@pytest.mark.asyncio
async def test_sandbox_applies_server_limits_when_worker_requests_more(monkeypatch) -> None:
    import base64
    from app import attachment_processing as processing

    monkeypatch.setattr(
        processing,
        "attachment_processing_limits",
        lambda: AttachmentProcessingLimits(max_bytes=4, chunk_chars=16),
    )
    response = await _parse_request(
        {
            "version": 1,
            "content_type": "application/pdf",
            "max_bytes": 50_000,
            "chunk_chars": 256,
            "data_b64": base64.b64encode(b"%PDF-1.4\n").decode("ascii"),
        }
    )
    assert response["ok"] is False
    assert response["status"] == "unreadable"
    assert response["error_code"] == "attachment_too_large"


@pytest.mark.asyncio
async def test_sandbox_server_times_out_stalled_client(monkeypatch) -> None:
    from app import attachment_sandbox as sandbox

    class Reader:
        async def readexactly(self, size):
            await asyncio.sleep(1)
            return b""

    class Writer:
        def __init__(self):
            self.payload = bytearray()
            self.closed = False

        def write(self, data):
            self.payload.extend(data)

        async def drain(self):
            return None

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    monkeypatch.setattr(sandbox, "server_request_timeout_seconds", lambda: 0.01)
    writer = Writer()
    await _handle_client(Reader(), writer)

    assert writer.closed is True
    assert b"parser_timeout" in bytes(writer.payload)


@pytest.mark.asyncio
async def test_sandbox_rejects_missing_resource_limits_as_protocol_error(tmp_path) -> None:
    import base64
    import json
    import struct

    socket_path = tmp_path / "parser.sock"
    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        body = json.dumps(
            {
                "version": 1,
                "content_type": "application/pdf",
                "data_b64": base64.b64encode(b"%PDF-1.4\n").decode("ascii"),
            }
        ).encode("utf-8")
        writer.write(struct.pack("!I", len(body)) + body)
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), timeout=5)
        size = struct.unpack("!I", header)[0]
        body = await asyncio.wait_for(reader.readexactly(size), timeout=5)
        response = json.loads(body.decode("utf-8"))
        assert response["ok"] is False
        assert response["error_code"] == "parser_protocol_error"
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    ("name", "reader"),
    [
        ("ATTACHMENT_PARSER_TIMEOUT_SECONDS", parser_timeout_seconds),
        ("ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS", server_request_timeout_seconds),
    ],
)
@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf"])
def test_parser_timeout_configuration_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    reader,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="finite number greater than zero"):
        reader()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
async def test_sandbox_client_rejects_non_finite_or_non_positive_explicit_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite and greater than zero"):
        await parse_attachment_sandboxed(
            b"%PDF-1.4\n",
            content_type="application/pdf",
            max_bytes=50_000,
            chunk_chars=256,
            socket_path="unused",
            timeout_seconds=timeout,
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":1,"version":1,"content_type":"application/pdf"}',
        b'{"version":1,"max_bytes":NaN,"chunk_chars":256,"data_b64":""}',
        b'{"version":1,"max_bytes":Infinity,"chunk_chars":256,"data_b64":""}',
    ],
)
async def test_sandbox_rejects_ambiguous_json_frames_as_protocol_error(
    tmp_path,
    raw: bytes,
) -> None:
    import json
    import struct

    socket_path = tmp_path / "parser.sock"
    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(struct.pack("!I", len(raw)) + raw)
        await writer.drain()

        header = await asyncio.wait_for(reader.readexactly(4), timeout=5)
        size = struct.unpack("!I", header)[0]
        body = await asyncio.wait_for(reader.readexactly(size), timeout=5)
        response = json.loads(body.decode("utf-8"))
        if response.get("ok") is not False:
            raise AssertionError(f"unexpected sandbox response: {response!r}")
        if response.get("error_code") != "parser_protocol_error":
            raise AssertionError(f"unexpected sandbox error: {response!r}")
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_sandbox_outbound_frame_rejects_non_standard_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        _frame({"version": 1, "value": value})
