# Immutable production image

## Problem

The production Compose file previously used `build: .` for migration, API, workers and scheduler. That allows the deployment host to reconstruct the application independently, so two nominally identical releases can run different bytes depending on source checkout, build context or dependency resolution.

## Contract

Production now requires `RPY_IMAGE` to be a registry reference pinned by a full `sha256` digest. The same digest is consumed by:

- `migrate`;
- `api`;
- `worker-1`;
- `worker-2`;
- `scheduler`.

Application services must not contain `build:` in `compose.production.yaml`.

`docker compose config` plus `scripts/validate_production_compose.py` enforce the rule in CI. Unit tests additionally reject mutable tags, malformed digests, source builds and mixed digests across application services.

## Operational consequence

A release is the digest, not a tag. CI should build once, publish once and record the registry digest. Staging and production should promote that same digest rather than rebuild the image.

Rollback selects a previously known-good digest. It does not rebuild source. Schema compatibility remains a separate migration concern; destructive database changes still require an explicit data/schema rollback strategy.
