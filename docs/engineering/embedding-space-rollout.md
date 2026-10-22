# Embedding-space rollout

Rpy is migrating vector retrieval from the legacy OpenAI `process_steps.embedding vector(1536)` column to provider/model-isolated `process_step_embeddings vector(1024)` rows.

## Deployment switch

`EMBEDDING_SPACE_RUNTIME_ENABLED=false` is the compatibility/rollback mode. It preserves the historical OpenAI path and does not read or write the new embedding table.

`EMBEDDING_SPACE_RUNTIME_ENABLED=true` enables the provider/model-isolated runtime. The active space is resolved by `EMBEDDING_PROVIDER` and supports exactly one provider per deployment.

### BGE-M3

BGE is the default self-hosted provider:

- `EMBEDDING_PROVIDER=bge`;
- model `BAAI/bge-m3`;
- 1024 dimensions;
- `BGE_EMBEDDING_PATH` points to the pre-provisioned local artifact;
- optional `BGE_EMBEDDING_DEVICE`;
- optional `BGE_EMBEDDING_USE_FP16`.

The deployment must use the BGE-capable image/artifact contract documented in `docs/deployment/bge-image.md`. CI does not download the model. Building a BGE-capable image alone is not rollout evidence: the historical reindex and real retrieval-quality evaluation remain separate gates.

### Cohere Embed v4

Cohere is implemented as an optional external provider:

- `EMBEDDING_PROVIDER=cohere`;
- `ALLOW_EXTERNAL_EMBEDDINGS=true` is mandatory;
- model `embed-v4.0`;
- 1024 dimensions;
- `COHERE_API_KEY` is worker-only;
- no automatic fallback to BGE, OpenAI or another provider.

External authorization is environment-specific and does not override secrecy. See `docs/engineering/external-embedding-authorization.md`.

## Reindex behavior

On a long-process vector retrieval with the new runtime enabled, Rpy:

1. finds movements for the requested version that do not have a vector in the exact active provider/model space;
2. encodes only those missing movements;
3. upserts rows in `process_step_embeddings` keyed by `step_id + provider + model`;
4. encodes the query in that same active semantic space;
5. searches only rows matching the exact provider/model space.

Existing vectors from other spaces remain untouched for controlled rollback or later deletion. They are never mixed in one ranking.

Historical reindexing follows the same provider/model isolation. Cohere reindexing excludes secret process versions, and the embedding runtime independently rejects external embedding of a secret version before movement text is sent to the provider.

## Complete historical reindex command

For the selected embedding space, the reindex tool can advance through every resumable batch
without an operator manually copying checkpoints:

```bash
python scripts/reindex_embedding_space.py --all --limit 25
```

Use `--dry-run --all` first on the target database to count incomplete versions without writing
vectors. The command fails if its checkpoint stops advancing. Its final JSON report records batch
count, versions inspected, versions with missing embeddings and embeddings written.

This is evidence only for the database/environment where it runs. BGE activation still requires the
real local artifact and retrieval-quality evidence.

## Rollback

To roll back during migration, disable `EMBEDDING_SPACE_RUNTIME_ENABLED` and keep the existing OpenAI configuration. The legacy `process_steps.embedding` column remains unchanged by the new runtime.

Do not switch provider/model by editing rows in place. A provider/model change requires re-embedding into its own space and validation before the deployment switch changes. Removing Cohere authorization means disabling `ALLOW_EXTERNAL_EMBEDDINGS` or selecting an approved BGE/legacy rollback path and performing the corresponding controlled reindex/rollback procedure.

## Secrecy and external providers

Embedding-provider authorization does not override process secrecy rules.

- secret process versions are never sent to Cohere;
- the normal RAG path short-circuits secret processes;
- historical Cohere reindex excludes secret versions;
- the runtime independently refuses external embedding for a secret version;
- Cohere is never an automatic fallback;
- automated tests use fake/local encoders and make no paid-provider calls.
