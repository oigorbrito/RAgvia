from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import asyncpg

from app.embedding_runtime import embedding_space_runtime_enabled, get_active_embedding_runtime
from app.tpu_glossary import load_legal_knowledge
from app.unicode_security import model_view_text


async def index(database_url: str) -> int:
    if not embedding_space_runtime_enabled():
        raise RuntimeError("EMBEDDING_SPACE_RUNTIME_ENABLED=true is required")
    runtime = get_active_embedding_runtime()
    if runtime.space.provider != "bge":
        raise RuntimeError("legal supplement indexing is local-BGE-only; external embeddings are prohibited")
    entries = load_legal_knowledge()
    texts = [model_view_text(" | ".join((entry.name, *entry.aliases, entry.definition))).text for entry in entries]
    vectors = await runtime.encoder.embed_documents(texts)
    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction():
            await conn.executemany(
                """INSERT INTO legal_knowledge_embeddings
                   (entry_id, embedding_content_sha256, provider, model, dimensions, embedding)
                   VALUES ($1,$2,$3,$4,$5,$6::vector)
                   ON CONFLICT (entry_id,definition_sha256,provider,model)
                   DO UPDATE SET dimensions=EXCLUDED.dimensions, embedding=EXCLUDED.embedding,
                                 created_at=NOW()""",
                [(entry.id, entry.embedding_sha256, runtime.space.provider,
                  runtime.space.model, runtime.space.dimensions, vector)
                 for entry, vector in zip(entries, vectors, strict=True)],
            )
        return len(entries)
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Index the pinned supplementary legal glossary with local BGE only")
    parser.add_argument("--database-url", default=os.environ.get("WORKER_DATABASE_URL"))
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or WORKER_DATABASE_URL is required")
    from scripts.verify_bge_image_runtime import validate
    errors = validate(model_dir=args.model_dir)
    if errors:
        parser.error("verified offline BGE artifact required: " + "; ".join(errors))
    print(f"indexed_entries={asyncio.run(index(args.database_url))}; provider=bge; model=BAAI/bge-m3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
