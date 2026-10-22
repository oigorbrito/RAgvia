# Claim-evidence provenance migration

## Scope

Migration `032_summary_claim_evidence.sql` adds deterministic claim-level provenance for generated process summaries.

The migration creates:

- `process_summary_claims`, one row per material claim in a persisted summary;
- `process_summary_claim_sources`, one or more authorized evidence references per claim.

Material claim identities are deterministic: `synthesis`, `current_status`, and zero-based list ids such as `timeline:0`, `attention:0`, `decisions:1`, `deadlines:0`, `related_processes:0`, and `attachments:0`.

## Evidence references

Evidence ids are application-generated opaque identifiers:

- `p-<32 hex>` for process/version metadata;
- `m-<32 hex>` for a selected movement;
- `a-<32 hex>` for an authorized attachment chunk.

The suffix is the corresponding UUID without dashes. The database trigger verifies that the persisted ref matches the referenced version, movement, or attachment chunk.

Movement and attachment claims are additionally constrained to sources actually persisted as used by the same summary. The trigger also checks the underlying movement/chunk process and version instead of trusting provenance rows alone.

## Publication behavior

For non-secret processes, a validated summary is publishable only when claim-level provenance exists. Legacy structured summaries may still be parsed for compatibility, but they do not become public merely because their old document-level validation passed.

Secret-process behavior remains separate: the application returns the restricted local summary path and must not send process contents or evidence refs to the external provider.

## Runtime permissions

`rpy_api` requires read-only access to both claim-provenance tables because public summary/source responses expose the safe claim mapping.

`rpy_worker` requires SELECT/INSERT/UPDATE/DELETE because generation replaces claim provenance transactionally with the summary.

No raw provider prompt, raw attachment text, or provider response is stored in the claim-provenance tables.

## Rollout checks

Before release, verify:

1. migrations apply on an existing database;
2. API and worker role provisioning includes the new tables;
3. valid process, movement, and attachment refs persist;
4. unknown or mismatched refs fail closed;
5. movement/chunk refs from another process or version fail;
6. duplicate claims and duplicate evidence refs fail at the validator/schema boundary;
7. every `attention` item also carries deterministic claim provenance;
8. deleting a summary cascades through claims and claim sources;
9. legacy non-secret summaries without claim provenance are not publishable.

## What this migration does not prove

Evidence refs prove identity, scope, authorization, and traceability of a source used for a claim. They do **not** prove by themselves that the claim is semantically entailed by the referenced text.

Semantic classification such as `supported`, `contradicted`, or `insufficient` belongs to the separate verification layer tracked by issue #267.
