from __future__ import annotations

import pytest

from app.attachments import AttachmentChunkInput, _validate_chunk, _validate_status


def test_attachment_chunk_hash_is_deterministic() -> None:
    first = AttachmentChunkInput(text="conteúdo sintético", page_start=1, page_end=1)
    second = AttachmentChunkInput(text="conteúdo sintético", page_start=1, page_end=1)
    assert first.content_sha256 == second.content_sha256
    assert len(first.content_sha256) == 64


@pytest.mark.parametrize(
    "chunk",
    [
        AttachmentChunkInput(text=""),
        AttachmentChunkInput(text="x", page_start=1),
        AttachmentChunkInput(text="x", page_end=1),
        AttachmentChunkInput(text="x", page_start=0, page_end=1),
        AttachmentChunkInput(text="x", page_start=3, page_end=2),
        AttachmentChunkInput(text="x", char_start=0),
        AttachmentChunkInput(text="x", char_end=1),
        AttachmentChunkInput(text="x", char_start=-1, char_end=1),
        AttachmentChunkInput(text="x", char_start=3, char_end=2),
    ],
)
def test_invalid_attachment_chunk_bounds_fail_closed(chunk: AttachmentChunkInput) -> None:
    with pytest.raises(ValueError):
        _validate_chunk(chunk)


@pytest.mark.parametrize(
    "status",
    ["pending", "ready", "unavailable", "corrupt", "unreadable"],
)
def test_attachment_status_contract_accepts_known_states(status: str) -> None:
    assert _validate_status(status) == status


def test_attachment_status_contract_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="invalid attachment status"):
        _validate_status("processed")
