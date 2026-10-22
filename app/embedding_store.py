from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import asyncpg

from app.embedding_spaces import EmbeddingSpace, assert_embedding_dimensions


async def upsert_step_embeddings(
    conn: asyncpg.Connection,
    *,
    space: EmbeddingSpace,
    items: Sequence[tuple[UUID, list[float]]],
) -> int:
    if not items:
        return 0
    for _, vector in items:
        assert_embedding_dimensions(vector, space=space)

    await conn.executemany(
        """
        INSERT INTO process_step_embeddings (
            step_id, provider, model, dimensions, embedding
        ) VALUES ($1, $2, $3, $4, $5::vector)
        ON CONFLICT (step_id, provider, model)
        DO UPDATE SET dimensions = EXCLUDED.dimensions,
                      embedding = EXCLUDED.embedding,
                      created_at = NOW()
        """,
        [
            (step_id, space.provider, space.model, space.dimensions, vector)
            for step_id, vector in items
        ],
    )
    return len(items)


async def vector_search_space(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    embedding: Sequence[float],
    space: EmbeddingSpace,
    limit: int = 30,
) -> dict[UUID, float]:
    if limit <= 0:
        raise ValueError("vector search limit must be positive")
    vector = [float(value) for value in embedding]
    assert_embedding_dimensions(vector, space=space)

    rows = await conn.fetch(
        """
        SELECT ps.id,
               1 - (pse.embedding <=> $4::vector) AS similarity
        FROM process_step_embeddings pse
        JOIN process_steps ps ON ps.id = pse.step_id
        WHERE ps.version_id = $1
          AND pse.provider = $2
          AND pse.model = $3
        ORDER BY pse.embedding <=> $4::vector, ps.step_number DESC
        LIMIT $5
        """,
        version_id,
        space.provider,
        space.model,
        vector,
        limit,
    )
    return {row["id"]: max(0.0, float(row["similarity"])) for row in rows}
