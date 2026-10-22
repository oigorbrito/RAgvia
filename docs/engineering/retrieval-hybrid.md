# Hybrid retrieval contract

Rpy uses two final ranking signals for long, non-secret processes when vector retrieval is configured:

- literal in-memory BM25 over the complete eligible movement corpus;
- pgvector cosine similarity.

PostgreSQL full-text search with `to_tsvector('portuguese', ...)` remains available for candidate discovery, isolation/index verification and specialized callers, but its `ts_rank_cd` value is not a final ranking signal.

The initial hybrid score is `0.5 * lexical + 0.5 * vector`, followed by the existing recency multiplier. Mandatory context remains independent of ranking: the first movement, last movement, five most recent movements, and recognized procedural milestones are always preserved.

Processes with at most 40 movements bypass retrieval and inject all movements in deterministic `step_number` order.

## Lexical source of truth

Literal BM25 is the production lexical source of truth. It is computed after the durable eligibility/version filters have loaded the movement corpus and is normalized before the documented 0.5 lexical / 0.5 vector fusion.

The PostgreSQL FTS helper remains version-scoped, uses the Portuguese text-search configuration and the `process_steps_fts_idx` index, but its `ts_rank_cd` score is reference/candidate metadata only. Supplying that map to the ranking API cannot replace BM25.

Using both BM25 and `ts_rank_cd` as independent final signals would overweight correlated lexical evidence and violate the intended 0.5 lexical / 0.5 vector split.

## Vector runtime selection and disabled mode

Vector retrieval has two explicit rollout modes:

- the historical OpenAI `vector(1536)` path is configured when the isolated runtime is disabled and `OPENAI_API_KEY` is present;
- the provider/model-isolated runtime is configured when `EMBEDDING_SPACE_RUNTIME_ENABLED=true` and resolves exactly one approved semantic space (local BGE by default or Cohere only with explicit external authorization).

If neither selected mode is actually configured, long-process retrieval remains operational using literal BM25 only and no embedding/query-vector provider call is attempted.

This is an intentional deployment mode, not an outage fallback. If the selected vector provider is configured and then fails, the error remains explicit instead of silently changing ranking semantics or crossing into another semantic space. BGE, Cohere and legacy OpenAI vectors are never mixed.

## Isolation

Both lexical and vector queries are scoped to the exact `version_id`. Cross-process/version matches must never enter the candidate score map. Tenant/process authorization occurs before provider exposure in the surrounding pipeline.

## Evidence

`tests/integration/test_retrieval_postgres.py` verifies that:

- PostgreSQL uses `process_steps_fts_idx` for the Portuguese lexical predicate when sequential scan is disabled for plan inspection;
- PostgreSQL FTS candidate discovery and literal BM25 identify the same synthetic target for exact/accented legal terms, while only BM25 feeds final ranking;
- pgvector identifies the same target from the synthetic embedding;
- a matching movement from another process/version is excluded from both score maps;
- the 0.5/0.5 hybrid still preserves forced milestones, the first/last movement, and the five most recent movements;
- a long process still produces a filtered RAG context with the legacy vector path unconfigured, while embedding functions are guarded to fail the test if called.

Provider/model-isolated runtime tests separately verify BGE/Cohere selection, dimensions, secrecy boundaries and no cross-provider fallback. No external provider is required for this evidence.