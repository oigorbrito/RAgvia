# TPU glossary RAG integration

This block wires the pinned TPU glossary into the process-summary RAG without changing the source process fields into derived legal explanations.

## Boundary

- Judit normalization keeps the received class name, subject names/codes and the received class code.
- TPU definitions are resolved only while building a non-secret RAG context.
- Unknown codes are omitted; there is no fuzzy matching or network fallback.
- Secret processes return before glossary resolution and therefore never send TPU-derived context to an external provider.
- The provider sees `tpu_glossary` as a separate derived context field. Original `class_name`, `subjects` and `header` remain separate.
- Exact glossary provenance is persisted with the summary: kind, code, TPU version, publisher, source, source reference and SHA-256 of the definition. Definition text is not persisted in provenance.
- Public source provenance may expose those safe identifiers and hashes, but not the definition text or raw provider prompt.

## Versioning

The active snapshot remains `data/tpu/2026-09-12.json`. A future snapshot update must be reviewed as a separate change. Existing summaries are not silently rewritten merely because a newer snapshot exists; regeneration is an explicit product/runtime action.

## Data minimization

Only definitions whose exact `(kind, code)` appears in the public process are injected. No complete TPU catalog is sent to the provider.
