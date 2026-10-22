# External embedding authorization

BGE-M3 is the default embedding provider. Cohere Embed v4 is an optional external provider and must never become an automatic fallback.

## Authorization boundary

A deployment may set `EMBEDDING_PROVIDER=cohere` only when the data/governance authority responsible for that environment has explicitly approved sending eligible process text to an external embedding provider. The deployer records that decision outside the repository according to the environment's normal change-control process and sets `ALLOW_EXTERNAL_EMBEDDINGS=true` only for the approved deployment.

Authorization is environment-specific. Approval in one environment does not authorize another environment or another dataset.

- CI and automated tests: external embeddings are never authorized; tests use local fakes and no real Cohere credentials.
- Local development: keep external embeddings disabled unless the developer is using non-sensitive/synthetic data under an explicit approved test arrangement.
- Staging or production: external embeddings remain disabled by default and require explicit environment-specific approval before `ALLOW_EXTERNAL_EMBEDDINGS=true` and `COHERE_API_KEY` are supplied.

## Non-negotiable safeguards

- Only one embedding provider is active per deployment.
- Cohere uses `embed-v4.0`, float embeddings and 1024 output dimensions.
- Switching provider/model requires controlled re-embedding/reindexing into its separate semantic space.
- BGE and Cohere vectors are never ranked together even though both use 1024 dimensions.
- Secret process versions are never sent to Cohere. The normal RAG path short-circuits secrets, the historical Cohere reindex excludes them, and the embedding runtime independently rejects external embedding of a secret version.
- A Cohere failure does not fall back to BGE, OpenAI or another provider; queue/provider retry policy handles retryable failures within the selected provider only.
- Removing external authorization means setting `ALLOW_EXTERNAL_EMBEDDINGS=false` or selecting BGE/legacy mode and performing the corresponding controlled reindex/rollback procedure.
