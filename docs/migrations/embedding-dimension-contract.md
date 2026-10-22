# Embedding dimension contract

## Scope

Keep the embedding provider configuration aligned with the migrated PostgreSQL schema.

## Contract

`process_steps.embedding` is `vector(1536)`. The application constant `VECTOR_DIMENSIONS = 1536` is therefore schema state, not an environment setting.

Changing vector dimensionality requires a deliberate SQL migration, re-embedding existing rows, and rebuilding the vector index. It must not be accomplished by changing a deployment variable alone.

## Supported provider models

Rpy accepts the OpenAI embedding models that are explicitly covered by the current contract:

- `text-embedding-3-small`
- `text-embedding-3-large`, requested with `dimensions=1536`
- `text-embedding-ada-002`, whose fixed output is compatible with 1536

Unknown models fail before provider/network work. Adding a future model requires an explicit review of its dimensionality behavior and a test update.

## Runtime defense

Provider responses are still validated for vector count, indexes and exact vector length before any database write.
