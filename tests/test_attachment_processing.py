from __future__ import annotations

import pytest

from app.attachment_processing import (
    AttachmentProcessingError,
    AttachmentProcessingLimits,
    chunk_attachment_text,
    AttachmentOCRConfig,
    attachment_ocr_config,
    normalize_content_type,
    parse_image_attachment_ocr,
    parse_text_attachment,
)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_attachment_ocr_config_rejects_non_finite_pdf_scale(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("ATTACHMENT_PDF_OCR_SCALE", value)

    with pytest.raises(
        RuntimeError,
        match="ATTACHMENT_PDF_OCR_SCALE must be a finite number greater than zero",
    ):
        attachment_ocr_config()


def test_content_type_normalization_strips_parameters():
    assert normalize_content_type(" Text/Plain; charset=UTF-8 ") == "text/plain"


def test_parse_text_attachment_normalizes_utf8_and_preserves_positions():
    chunks = parse_text_attachment(
        b"Primeira linha\r\nSegunda linha\r\nTerceira linha",
        content_type="text/plain; charset=utf-8",
        limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
    )
    assert len(chunks) == 1
    assert chunks[0].text == "Primeira linha\nSegunda linha\nTerceira linha"
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(chunks[0].text)


def test_chunking_is_deterministic_without_overlap():
    text = ("abc " * 200).strip()
    chunks = chunk_attachment_text(text, chunk_chars=256)
    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.char_end is not None
        assert current.char_start is not None
        assert previous.char_end <= current.char_start
    reconstructed = " ".join(chunk.text for chunk in chunks)
    assert reconstructed.replace("  ", " ") == text


@pytest.mark.parametrize(
    ("data", "content_type", "status", "error_code"),
    [
        (b"", "text/plain", "unreadable", "empty_attachment"),
        (b"\xff\xfe", "text/plain", "corrupt", "invalid_utf8"),
        (b"abc\x00def", "text/plain", "corrupt", "invalid_text_bytes"),
        (b"texto", "application/pdf", "unreadable", "unsupported_content_type"),
    ],
)
def test_parse_text_attachment_classifies_failures(data, content_type, status, error_code):
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_text_attachment(
            data,
            content_type=content_type,
            limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
        )
    assert exc_info.value.status == status
    assert exc_info.value.error_code == error_code


def test_parse_text_attachment_rejects_oversized_payload():
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_text_attachment(
            b"a" * 11,
            content_type="text/plain",
            limits=AttachmentProcessingLimits(max_bytes=10, chunk_chars=256),
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "attachment_too_large"



@pytest.mark.asyncio
async def test_local_png_ocr_is_opt_in_and_chunked(monkeypatch):
    from app import attachment_processing as processing

    observed = {}

    def fake_ocr(data, *, suffix, config):
        observed["data"] = data
        observed["suffix"] = suffix
        observed["language"] = config.language
        return "Texto reconhecido\r\nsegunda linha"

    monkeypatch.setattr(processing, "_run_tesseract_ocr", fake_ocr)
    data = b"\x89PNG\r\n\x1a\nsynthetic"
    chunks = await parse_image_attachment_ocr(
        data,
        content_type="image/png",
        limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
        config=AttachmentOCRConfig(
            enabled=True,
            binary="tesseract",
            language="por",
            timeout_seconds=5,
        ),
    )

    assert observed == {"data": data, "suffix": ".png", "language": "por"}
    assert len(chunks) == 1
    assert chunks[0].text == "Texto reconhecido\nsegunda linha"


@pytest.mark.asyncio
async def test_local_jpeg_ocr_accepts_valid_magic(monkeypatch):
    from app import attachment_processing as processing

    monkeypatch.setattr(
        processing,
        "_run_tesseract_ocr",
        lambda data, *, suffix, config: "Documento JPEG",
    )
    chunks = await parse_image_attachment_ocr(
        b"\xff\xd8\xffsynthetic",
        content_type="image/jpeg",
        limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
        config=AttachmentOCRConfig(enabled=True),
    )
    assert chunks[0].text == "Documento JPEG"


@pytest.mark.asyncio
async def test_image_ocr_disabled_is_unreadable_without_running_engine(monkeypatch):
    from app import attachment_processing as processing

    monkeypatch.setattr(
        processing,
        "_run_tesseract_ocr",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_image_attachment_ocr(
            b"\x89PNG\r\n\x1a\nsynthetic",
            content_type="image/png",
            limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
            config=AttachmentOCRConfig(enabled=False),
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "ocr_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "content_type", "error_code", "status"),
    [
        (b"not-png", "image/png", "invalid_image_header", "corrupt"),
        (b"not-jpeg", "image/jpeg", "invalid_image_header", "corrupt"),
        (b"", "image/png", "empty_attachment", "unreadable"),
    ],
)
async def test_image_ocr_validates_payload_before_engine(
    monkeypatch, data, content_type, error_code, status
):
    from app import attachment_processing as processing

    monkeypatch.setattr(
        processing,
        "_run_tesseract_ocr",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_image_attachment_ocr(
            data,
            content_type=content_type,
            limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
            config=AttachmentOCRConfig(enabled=True),
        )
    assert exc_info.value.status == status
    assert exc_info.value.error_code == error_code


@pytest.mark.asyncio
async def test_image_ocr_empty_result_is_unreadable(monkeypatch):
    from app import attachment_processing as processing

    monkeypatch.setattr(
        processing,
        "_run_tesseract_ocr",
        lambda data, *, suffix, config: "  \n ",
    )
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_image_attachment_ocr(
            b"\x89PNG\r\n\x1a\nsynthetic",
            content_type="image/png",
            limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
            config=AttachmentOCRConfig(enabled=True),
        )
    assert exc_info.value.error_code == "ocr_no_text"



def test_tesseract_runner_maps_missing_binary_and_timeout(monkeypatch):
    import subprocess
    from app import attachment_processing as processing

    def missing(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(processing.subprocess, "run", missing)
    with pytest.raises(AttachmentProcessingError) as exc_info:
        processing._run_tesseract_ocr(
            b"\x89PNG\r\n\x1a\nsynthetic",
            suffix=".png",
            config=AttachmentOCRConfig(enabled=True, binary="missing", timeout_seconds=1),
        )
    assert exc_info.value.error_code == "ocr_unavailable"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=1)

    monkeypatch.setattr(processing.subprocess, "run", timeout)
    with pytest.raises(AttachmentProcessingError) as exc_info:
        processing._run_tesseract_ocr(
            b"\x89PNG\r\n\x1a\nsynthetic",
            suffix=".png",
            config=AttachmentOCRConfig(enabled=True, timeout_seconds=1),
        )
    assert exc_info.value.error_code == "ocr_timeout"


def test_tesseract_runner_uses_private_temporary_file_and_deletes_it(monkeypatch):
    from types import SimpleNamespace
    from app import attachment_processing as processing

    observed = {}

    def fake_run(args, **kwargs):
        image_path = args[1]
        observed["path"] = image_path
        observed["exists_during"] = processing.Path(image_path).exists()
        observed["bytes"] = processing.Path(image_path).read_bytes()
        observed["args"] = args
        return SimpleNamespace(returncode=0, stdout="Texto OCR".encode("utf-8"))

    monkeypatch.setattr(processing.subprocess, "run", fake_run)
    data = b"\x89PNG\r\n\x1a\nsynthetic"
    text = processing._run_tesseract_ocr(
        data,
        suffix=".png",
        config=AttachmentOCRConfig(
            enabled=True,
            binary="tesseract",
            language="por",
            timeout_seconds=5,
        ),
    )

    assert text == "Texto OCR"
    assert observed["exists_during"] is True
    assert observed["bytes"] == data
    assert observed["args"][0] == "tesseract"
    assert observed["args"][2] == "stdout"
    assert not processing.Path(observed["path"]).exists()
