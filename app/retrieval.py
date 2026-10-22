from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

import asyncpg

SHORT_PROCESS_ALL_STEPS_MAX = 40
DEFAULT_RANK_LIMIT = 20
LEXICAL_WEIGHT = 0.5
VECTOR_WEIGHT = 0.5
RECENCY_BOOST_MAX = 0.3
MANDATORY_RECENT_STEPS = 5

# Retrieval is shared by tenant-authorized API flows and background summary jobs.
# Tenant authorization happens before a process/version reaches this layer; this
# predicate revalidates the durable process/version/CNJ boundary and excludes
# movement-level privacy/secrecy mismatches before context or reranking.
ELIGIBLE_PROCESS_STEP_SQL = """
    ps.process_id = pv.process_id
    AND p.id = pv.process_id
    AND COALESCE(NULLIF(ps.metadata->>'cnj', ''), p.code) = p.code
    AND (
        NOT (ps.metadata ? 'instance')
        OR NOT (p.header ? 'instance')
        OR ps.metadata->>'instance' = p.header->>'instance'
    )
    AND lower(COALESCE(ps.metadata->>'private', 'false'))
        NOT IN ('true', '1', 'yes')
    AND p.secrecy_level = 0
    AND CASE
        WHEN COALESCE(ps.metadata->>'secrecy_level', '') ~ '^[0-9]+$'
        THEN (ps.metadata->>'secrecy_level')::integer
        ELSE 0
    END = 0
"""

_TOKEN_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
MILESTONE_RE = re.compile(
    r"\b("
    r"SENTEN[CÇ]A|AC[ÓO]RD[ÃA]O|LIMINAR|TUTELA|CITA[CÇ][ÃA]O|AUDI[ÊE]NCIA|"
    r"TR[ÂA]NSITO\s+EM\s+JULGADO|ARQUIVAMENTO|EXTIN[CÇ][ÃA]O|PER[ÍI]CIA|PENHORA|"
    r"BAIXA\s+DEFINITIVA|RECURSO|APELA[CÇ][ÃA]O|EMBARGOS|DECIS[ÃA]O"
    r")\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Step:
    id: UUID
    step_number: int
    text: str
    title: str | None = None
    occurred_at: Any = None
    source_step_number: int | None = None

    @property
    def searchable_text(self) -> str:
        return f"{self.title or ''} {self.text}".strip()


@dataclass(slots=True)
class RankedStep:
    step: Step
    bm25: float = 0.0
    lexical: float = 0.0
    vector: float = 0.0
    score: float = 0.0
    forced: bool = False


def tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def bm25_scores(
    query: str,
    steps: Sequence[Step],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[UUID, float]:
    """Compute the authoritative lexical score used by long-process ranking.

    PostgreSQL full-text search may still be used for candidate discovery or index
    verification, but its ranking function is not a substitute for this BM25 score.
    """
    if not steps:
        return {}
    query_terms = tokenize(query)
    if not query_terms:
        return {step.id: 0.0 for step in steps}

    docs = [tokenize(step.searchable_text) for step in steps]
    avgdl = sum(len(doc) for doc in docs) / len(docs) or 1.0
    n_docs = len(docs)
    doc_freq: dict[str, int] = {}
    for term in set(query_terms):
        doc_freq[term] = sum(1 for doc in docs if term in doc)

    scores: dict[UUID, float] = {}
    for step, doc in zip(steps, docs, strict=True):
        frequencies: dict[str, int] = {}
        for token in doc:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        doc_len = len(doc)
        for term in query_terms:
            tf = frequencies.get(term, 0)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            denominator = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += idf * ((tf * (k1 + 1)) / denominator)
        scores[step.id] = score
    return scores


def _normalize(values: dict[UUID, float]) -> dict[UUID, float]:
    if not values:
        return {}
    maximum = max(values.values(), default=0.0)
    if maximum <= 0:
        return {key: 0.0 for key in values}
    return {key: value / maximum for key, value in values.items()}


def rank_steps(
    *,
    query: str,
    steps: Sequence[Step],
    lexical_scores: dict[UUID, float] | None = None,
    vector_scores: dict[UUID, float] | None = None,
    limit: int = DEFAULT_RANK_LIMIT,
) -> list[RankedStep]:
    if len(steps) <= SHORT_PROCESS_ALL_STEPS_MAX:
        return [
            RankedStep(step=step, forced=True, score=1.0)
            for step in sorted(steps, key=lambda item: item.step_number)
        ]

    bm25 = _normalize(bm25_scores(query, steps))
    # BM25 is the authoritative lexical signal. lexical_scores is retained as a
    # compatibility/candidate-discovery input only and must never replace the
    # final lexical score (for example with PostgreSQL ts_rank_cd).
    lexical = bm25
    vector = _normalize(vector_scores or {})
    max_step = max(step.step_number for step in steps) or 1

    ranked: list[RankedStep] = []
    for step in steps:
        base_score = (
            LEXICAL_WEIGHT * lexical.get(step.id, 0.0)
            + VECTOR_WEIGHT * vector.get(step.id, 0.0)
        )
        recency = 1.0 + RECENCY_BOOST_MAX * (step.step_number / max_step)
        forced = bool(MILESTONE_RE.search(step.searchable_text))
        ranked.append(
            RankedStep(
                step=step,
                bm25=bm25.get(step.id, 0.0),
                lexical=lexical.get(step.id, 0.0),
                vector=vector.get(step.id, 0.0),
                score=base_score * recency,
                forced=forced,
            )
        )

    by_number = sorted(ranked, key=lambda item: item.step.step_number)
    mandatory_ids = {by_number[0].step.id, by_number[-1].step.id}
    mandatory_ids.update(item.step.id for item in by_number[-MANDATORY_RECENT_STEPS:])
    mandatory_ids.update(item.step.id for item in ranked if item.forced)

    selected: dict[UUID, RankedStep] = {
        item.step.id: item for item in ranked if item.step.id in mandatory_ids
    }
    for item in sorted(ranked, key=lambda candidate: candidate.score, reverse=True):
        if len(selected) >= max(limit, len(mandatory_ids)):
            break
        selected.setdefault(item.step.id, item)

    return sorted(selected.values(), key=lambda item: item.step.step_number)


def rerank_steps(
    *,
    steps: Sequence[Step],
    scores: dict[UUID, float],
    limit: int = 15,
) -> list[Step]:
    """Apply injected reranker scores while preserving mandatory movements."""
    if limit <= 0:
        raise ValueError("reranker limit must be positive")
    if not steps:
        return []

    by_number = sorted(steps, key=lambda item: item.step_number)
    mandatory_ids = {by_number[0].id, by_number[-1].id}
    mandatory_ids.update(item.id for item in by_number[-MANDATORY_RECENT_STEPS:])
    mandatory_ids.update(
        item.id for item in by_number if MILESTONE_RE.search(item.searchable_text)
    )

    selected = {item.id: item for item in by_number if item.id in mandatory_ids}
    ranked = sorted(
        by_number,
        key=lambda item: (-float(scores.get(item.id, 0.0)), item.step_number),
    )
    for item in ranked:
        if len(selected) >= max(limit, len(mandatory_ids)):
            break
        selected.setdefault(item.id, item)
    return sorted(selected.values(), key=lambda item: item.step_number)


async def _eligible_score_map(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    scores: dict[UUID, float],
) -> dict[UUID, float]:
    if not scores:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT ps.id
        FROM process_steps ps
        JOIN process_versions pv ON pv.id = ps.version_id
        JOIN processes p ON p.id = pv.process_id
        WHERE ps.version_id = $1
          AND ps.id = ANY($2::uuid[])
          AND {ELIGIBLE_PROCESS_STEP_SQL}
        """,
        version_id,
        list(scores),
    )
    eligible = {row["id"] for row in rows}
    return {step_id: score for step_id, score in scores.items() if step_id in eligible}


async def load_steps(conn: asyncpg.Connection, *, version_id: UUID) -> list[Step]:
    rows = await conn.fetch(
        f"""
        SELECT ps.id,
               ps.step_number,
               ps.title,
               ps.text,
               ps.occurred_at,
               CASE
                   WHEN (ps.metadata->>'source_step_number') ~ '^[0-9]+$'
                   THEN (ps.metadata->>'source_step_number')::integer
                   ELSE NULL
               END AS source_step_number
        FROM process_steps ps
        JOIN process_versions pv ON pv.id = ps.version_id
        JOIN processes p ON p.id = pv.process_id
        WHERE ps.version_id = $1
          AND {ELIGIBLE_PROCESS_STEP_SQL}
        ORDER BY ps.step_number ASC
        """,
        version_id,
    )
    return [
        Step(
            id=row["id"],
            step_number=int(row["step_number"]),
            title=row["title"],
            text=row["text"],
            occurred_at=row["occurred_at"],
            source_step_number=(
                int(row["source_step_number"])
                if row["source_step_number"] is not None
                else None
            ),
        )
        for row in rows
    ]


async def lexical_search(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    query: str,
    limit: int = 40,
) -> dict[UUID, float]:
    """Return version-scoped PostgreSQL FTS candidates.

    The returned ts_rank_cd values are for candidate discovery and index
    verification only. Final hybrid ranking always recomputes lexical relevance
    with bm25_scores over the eligible movement corpus.
    """
    rows = await conn.fetch(
        f"""
        SELECT ps.id,
               ts_rank_cd(
                   to_tsvector('portuguese', coalesce(ps.title, '') || ' ' || ps.text),
                   websearch_to_tsquery('portuguese', $2)
               ) AS lexical_rank
        FROM process_steps ps
        JOIN process_versions pv ON pv.id = ps.version_id
        JOIN processes p ON p.id = pv.process_id
        WHERE ps.version_id = $1
          AND {ELIGIBLE_PROCESS_STEP_SQL}
          AND to_tsvector('portuguese', coalesce(ps.title, '') || ' ' || ps.text)
              @@ websearch_to_tsquery('portuguese', $2)
        ORDER BY lexical_rank DESC, ps.step_number DESC
        LIMIT $3
        """,
        version_id,
        query,
        limit,
    )
    return {row["id"]: max(0.0, float(row["lexical_rank"])) for row in rows}


async def vector_search(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    embedding: Sequence[float],
    limit: int = 30,
) -> dict[UUID, float]:
    from app.embedding_runtime import (
        embedding_space_runtime_enabled,
        get_active_embedding_runtime,
    )

    if embedding_space_runtime_enabled():
        scores = await get_active_embedding_runtime().vector_search(
            conn,
            version_id=version_id,
            embedding=[float(value) for value in embedding],
            limit=limit,
        )
        return await _eligible_score_map(conn, version_id=version_id, scores=scores)

    rows = await conn.fetch(
        f"""
        SELECT ps.id, 1 - (ps.embedding <=> $2::vector) AS similarity
        FROM process_steps ps
        JOIN process_versions pv ON pv.id = ps.version_id
        JOIN processes p ON p.id = pv.process_id
        WHERE ps.version_id = $1
          AND ps.embedding IS NOT NULL
          AND {ELIGIBLE_PROCESS_STEP_SQL}
        ORDER BY ps.embedding <=> $2::vector
        LIMIT $3
        """,
        version_id,
        list(embedding),
        limit,
    )
    return {row["id"]: max(0.0, float(row["similarity"])) for row in rows}
