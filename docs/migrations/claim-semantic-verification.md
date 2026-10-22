# Claim semantic verification migration

Migration `033_claim_semantic_verification.sql` extends the claim provenance tables
introduced by #250. It does not replace or weaken the structural publication gate.

## Stored verification state

`process_summary_claims` stores the aggregate claim status and reason.
`process_summary_claim_sources` stores the status/reason for each cited evidence
relation plus the excerpt SHA-256 and available page/character ranges.

Literal provider-visible excerpts are stored separately in
`process_summary_claim_evidence_excerpts`. That table is intentionally excluded from
the `rpy_api` role and is readable/writable only by the worker (with backup read access),
so public API database credentials cannot retrieve the literal audit text.

Allowed statuses are `supported`, `contradicted`, `insufficient`, and
`not_evaluated`. Existing rows migrate safely as `not_evaluated`.

## Publication behavior

Structural provenance remains mandatory. The semantic layer adds a fail-closed rule:
a persisted aggregate claim or cited relation marked `contradicted` is not publishable.

During generation, deterministic contradiction or insufficient deterministic support is
fed into the existing single correction attempt. A second validation failure is stored
with the summary as a failed validation result rather than published.

`not_evaluated` is deliberately distinct from support. It remains publishable in this
baseline so that the system does not invent semantic certainty for prose outside the
deterministic verifier's coverage.

## Evidence boundary

Verification uses only application-authorized evidence that actually crossed the provider
boundary:

- normalized process metadata for process refs;
- bounded movement text/metadata for movement refs;
- bounded attachment chunks for attachment refs.

The verifier currently checks sentence-level exact text and deterministic CNJ, date,
amount, known-party, and movement-count anchors. It does not infer general legal entailment.

## Public API

Literal evidence excerpts are audit-only, live in the worker/backup-only excerpt table,
and are not returned by the public v1 API. Public JSON may expose verification
status/reason, evidence ref/kind/order, excerpt hash, and available page/character ranges.

Restricted-process behavior remains local/provider-free and does not expose claim evidence.

## Rollback and compatibility

The migration is additive. Existing claim rows receive `not_evaluated` defaults, so
legacy structurally valid summaries remain readable unless another publication invariant
fails. Application code must tolerate `not_evaluated` and must never treat it as
`supported`.
