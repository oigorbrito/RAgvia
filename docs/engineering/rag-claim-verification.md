# RAG claim verification

## Current deterministic layer

Rpy's claim-level verification is intentionally split into structural provenance and semantic verification.

Issue #250 implements the structural layer. For each material structured-summary field, the provider returns a deterministic `claim_id`, text identical to the corresponding structured field, and one or more `evidence_refs` supplied by the application.

The application then validates, before publication:

- the claim id is expected for the structured payload;
- every expected material field has exactly one claim identity;
- claim text matches the structured field;
- each claim has at least one evidence ref;
- every ref has a valid opaque format;
- every ref exists in the authorized evidence catalog for that generation;
- duplicate or unknown refs fail closed;
- persisted refs remain scoped to the same summary/process/version.

This validation is deterministic and does not call another model.

## Material coverage

Required provenance currently covers:

- `synthesis`;
- `current_status`;
- every item in `timeline`;
- every item in `attention`;
- every item in `decisions`;
- every item in `deadlines`;
- every item in `related_processes`;
- every item in `attachments`.

`attention` is covered because it is part of the provider-authored structured output and may contain factual conflicts or objective warnings. Some attention text is constrained by deterministic application warnings, such as source gaps or attachment-processing status, but the published sentence still crosses the provider boundary and therefore carries a claim identity and authorized evidence refs like the other structured fields. This remains structural provenance only; it does not assert semantic entailment of the warning from a particular ref.

## Process-level refs

A process/version ref may support a claim only when the claimed fact is actually present in structured process metadata supplied to generation.

Procedural events, decisions, deadlines, attachment facts, and similar event-level assertions should use movement or attachment refs when those are the actual supporting sources.

The deterministic validator checks reference identity and scope; it does not infer which source *ought* to support a sentence.

## Deterministic semantic baseline

Issue #267 builds on the structural provenance layer with a deliberately narrow
deterministic verifier. It does not introduce a second LLM.

For every cited claim/evidence relation the application records one of:

- `supported`: exact normalized claim text is present in the cited provider-visible
  source, or all deterministic fact anchors checked for the claim are present across
  its cited sources;
- `contradicted`: a cited canonical process source conflicts with an unambiguous
  deterministic field such as labeled process amount or total movement count. Any cited
  contradiction makes the aggregate claim contradicted;
- `insufficient`: the claim contains a deterministic CNJ, date, amount, known party, or
  movement-count anchor that is not present in the cited evidence set;
- `not_evaluated`: no deterministic fact anchor is available, so the application does
  not pretend to have established semantic entailment.

The verifier evaluates only the text and structured metadata that were actually supplied
to generation. Movement checks use the provider-visible bounded movement representation;
attachment checks use the bounded chunk representation. Process-level checks use the
normalized process projection represented by the process evidence ref.

A `contradicted` claim fails closed before publication. Deterministic `insufficient`
claims and intrinsically factual claim classes (`procedural_event`, `decision`,
`deadline`, `related_process`, and `attachment`) that remain `not_evaluated`
participate in the existing single correction attempt; a second failure remains
persisted in the normal validation result. Narrative `synthesis`, `current_status`,
and generic `attention` prose remain measurable as `not_evaluated` when they contain
no deterministic anchor, rather than being treated as automatically material. Any of
those fields that do contain deterministic facts are still subject to the normal
supported/insufficient/contradicted checks.

When available, the durable audit layer stores a bounded literal evidence excerpt in a
worker/backup-only table. The API-readable claim relation stores its SHA-256 and available
attachment page/character ranges. Literal excerpts remain internal: the public v1 JSON
representation exposes verification status/reason, refs, hashes and positions but never
the excerpt itself.

## Remaining semantic boundary

This deterministic baseline still does not establish general natural-language entailment.
It intentionally avoids heuristics for broad legal or procedural statements whose truth
cannot be established from exact text or the deterministic anchors above.

In particular, `not_evaluated` does not mean supported. A probabilistic verifier or
second model should be introduced only if evaluation demonstrates a material residual
unsupported-assertion rate and the added cost/failure modes are justified.

## Evaluation

Evaluation must keep structural provenance and semantic verification separate. At minimum
the report should include:

- count of material claims;
- structural failures: missing, unknown, stale or cross-version refs and text mismatches;
- claim counts by `supported`, `contradicted`, `insufficient`, and `not_evaluated`;
- deterministic retry/failure counts;
- publishable vs rejected summaries due to claim verification;
- residual unsupported-assertion rate measured independently of structural provenance.

Structural provenance success must never be counted as semantic support, and
`not_evaluated` must never be counted as `supported`.
