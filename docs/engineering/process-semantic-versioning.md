# Semantic process versioning

Rpy preserves each authorized Judit lawsuit response as a raw staged `process_version`, but it does not promote, reindex or regenerate a summary when the normalized process semantics are unchanged.

## Semantic document

The comparison is computed from normalized data after Judit normalization, not from callback envelopes or raw JSON bytes. The current semantic schema is version `3` and includes:

- normalized process `court`, `class_name`, `secrecy_level` and `header`;
- normalized `parties`, `representatives` and `subjects`;
- movement order and normalized `step_number`;
- localized movement instant, title and normalized text;
- source movement number, privacy flag, movement secrecy level and tags.

Transport identifiers, callback IDs, request IDs, response IDs, raw timestamp formatting and other envelope-only values do not affect semantic equality.

The canonical semantic document is serialized with sorted JSON keys and hashed with SHA-256. `process_versions.semantic_schema_version` records which document contract produced the hash.

## Unchanged response

When a staged candidate has the same semantic fingerprint as the current finalized version:

1. the raw staged version is retained for auditability;
2. the candidate is finalized with its semantic fingerprint;
3. `equivalent_to_version_id` points to the current version;
4. the current version is not replaced;
5. no `process_steps` are written for the equivalent candidate;
6. no embedding/reindex work is triggered for that candidate;
7. no `generate_process_summary` job is enqueued, so the already-current summary remains reusable.

The finalizer reports `finalized_unchanged` for this case.

## Changed response

A changed normalized movement, relevant structured field, privacy/secrecy metadata or other field included in semantic schema version 3 produces a different fingerprint. Normal version ordering then applies: a non-stale candidate is promoted, its steps are persisted and a non-cached response may enqueue summary generation.

Explicit Judit cached responses retain the existing no-generation rule independently of semantic comparison.

## Concurrency and immutability

Semantic comparison runs while the process row is locked with `FOR UPDATE`, so concurrent finalizers compare against a stable current version. Finalized semantic fingerprint metadata and `equivalent_to_version_id` are immutable under the same finalized-version trigger that protects source identity and raw payload fields.

Changing which normalized fields define equality requires a new semantic schema version and tests demonstrating the intended compatibility behavior; silently reinterpreting an existing fingerprint version is not allowed.
