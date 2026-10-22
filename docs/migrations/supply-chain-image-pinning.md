# Supply-chain image pinning

Production and build-critical container references are pinned to immutable registry digests.

## Pinned references

- `Dockerfile`: `python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`
- production PostgreSQL: `pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`
- backup/restore drill PostgreSQL client: the same pgvector digest.

The human-readable tags remain in the references to communicate the intended release line, while the digest determines the bytes actually pulled.

## Update procedure

Digest updates are deliberate dependency changes. Resolve the desired upstream tag to its current multi-platform index digest, update the repository references, and require the full CI suite (container build/import, restore drill, PostgreSQL integration) to pass before promotion.

The production Compose validator rejects a PostgreSQL image that falls back to a mutable tag.
