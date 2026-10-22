# Hybrid retrieval migration

## Expensive capability

PostgreSQL-native retrieval for long judicial proceedings, combining lexical BM25 and pgvector similarity without importing a general RAG framework or external vector database.

## Donor references

- `Azure-Samples/rag-postgres-openai-python`: vector SQL, PostgreSQL retrieval organization, hybrid retrieval patterns.
- `link178/legal-rag-engine`: retrieval/context separation and score fusion concepts.

## Classification

| Element | Decision | Rationale |
| --- | --- | --- |
| pgvector cosine search | ADAPT | Keep database-native semantic retrieval. |
| PostgreSQL FTS ranking from Azure sample | REFERENCE_ONLY | Rpy requires literal BM25, so `ts_rank_cd` is not used as the lexical score. |
| RRF from donor projects | DROP | Product requirement specifies 0.5 BM25 + 0.5 vector score, not RRF. |
| Azure/OpenAI chat abstractions | DROP | Generation is Anthropic and provider-isolated. |
| Generic document/chunk pipeline | DROP | One judicial movement is one chunk with no overlap. |
| External vector stores | DROP | pgvector is authoritative. |

## Rpy implementation

- `app/retrieval.py`
  - literal BM25 with `k1=1.5`, `b=0.75` and document-length normalization;
  - vector search with pgvector cosine distance;
  - normalized `0.5 * bm25 + 0.5 * vector`;
  - recency multiplier `1 + 0.3 * step_number / max_step_number`;
  - milestone forcing;
  - first, last and five most recent movements forced into context.
- `app/embeddings.py`
  - embeddings generated only when a finalized process contains more than 40 movements;
  - missing movement embeddings are generated in batches and persisted in PostgreSQL;
  - short processes do not invoke an embedding provider.
- `app/rag.py`
  - scalar process metadata is injected directly;
  - secret cases return before movements or embeddings are loaded;
  - long cases ensure movement embeddings, embed the fixed retrieval query and execute vector search before ranking.
- `app/db.py`
  - pgvector codec is registered on every asyncpg connection.

## Dependencies introduced

- `openai` is used only as the current embedding client. It is not used for generation or orchestration.
- `pgvector` already existed and remains the only vector storage integration.

## Runtime configuration

- `OPENAI_API_KEY`: required only when vector retrieval is actually needed.
- `EMBEDDING_MODEL`: optional override; defaults to `text-embedding-3-small`, matching the current `vector(1536)` schema.

## Tests / invariants

- Process with 40 or fewer movements returns all movements and bypasses vector ranking.
- Long processes force first, last, recent and milestone movements.
- Recency changes ranking of otherwise equal hits.
- Semantic/vector score contributes 50% of the base hybrid score.
- No Pinecone, LangChain or LlamaIndex dependencies are introduced.
