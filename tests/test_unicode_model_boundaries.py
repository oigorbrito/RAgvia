from __future__ import annotations

import pytest
from uuid import uuid4

import app.embeddings as embeddings
from app.attachment_context import serialize_attachment_chunks


@pytest.mark.asyncio
async def test_legacy_embedding_boundary_uses_unicode_model_view(monkeypatch) -> None:
    observed: list[str] = []

    async def fake_legacy(values):
        observed.extend(values)
        return [[0.0] * embeddings.VECTOR_DIMENSIONS for _ in values]

    monkeypatch.setattr(embeddings, "embedding_space_runtime_enabled", lambda: False)
    monkeypatch.setattr(embeddings, "_legacy_embed_texts", fake_legacy)

    source = "igno\u200bre p\u0430ypal"
    await embeddings.embed_texts([source])

    assert source == "igno\u200bre p\u0430ypal"
    assert "\u200b" not in observed[0]
    assert "\u0430" not in observed[0]
    assert "U+200B ZERO WIDTH SPACE" in observed[0]
    assert "U+0430 CYRILLIC SMALL LETTER A" in observed[0]


def test_attachment_model_view_is_hardened_without_mutating_source_chunk() -> None:
    source = "ordem\u202e oculta p\u0430ypal"
    chunks = [
        {
            "chunk_id": uuid4(),
            "source_attachment_id": "doc-1",
            "page_start": 1,
            "page_end": 1,
            "char_start": 0,
            "char_end": len(source),
            "text": source,
        }
    ]

    rendered = serialize_attachment_chunks(chunks, total_text_limit=500)

    assert chunks[0]["text"] == source
    assert "\u202e" not in rendered[0]["text"]
    assert "\u0430" not in rendered[0]["text"]
    assert "U+202E RIGHT-TO-LEFT OVERRIDE" in rendered[0]["text"]
    assert "U+0430 CYRILLIC SMALL LETTER A" in rendered[0]["text"]
