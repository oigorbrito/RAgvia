# Judit normalization contract

Rpy keeps the Judit callback payload as the source of truth and builds a separate normalized view for retrieval, validation and generation. Normalization must never mutate or replace `process_versions.source_payload`.

## Raw source versus normalized fields

The raw `lawsuit` callback is retained in `process_versions.source_payload` for traceability. Fields promoted to `processes` and `process_steps` are derived from that payload and may be normalized or minimized before use by the RAG pipeline.

An external Judit `response_type=summary` is not a RAG source. It may remain in the webhook delivery audit trail, but it is not promoted into the normalized process context.

## Parties and personal identifiers

Normalized process actors are classified before promotion. Entries whose explicit `person_type` identifies a lawyer, attorney, procurador, representative or public defender are removed from `parties` and stored in the separate normalized `representatives` collection. Rpy does not infer a represented party from a shared `side`; a `represents` value is preserved only when the source explicitly supplies a usable relationship field.

Normalized parties and representatives contain only the fields required by the application:

- `name`;
- `side`;
- `person_type`;
- `masked_person_id`, when the source exposes an 11- or 14-digit personal/company identifier.

`masked_person_id` reveals only the final two digits. Current representations are `***.***.***-NN` for 11 digits and `**.***.***/****-NN` for 14 digits. The complete identifier is not copied into the normalized party representation or provider context. Values that do not reduce to exactly 11 or 14 digits are omitted rather than guessed.

The public process view and deterministic summary renderer continue to consume `parties` only, so a representative cannot become a process party merely because Judit placed the record inside `parties[]`. The RAG context keeps representatives under their own key, while party-name validation remains bound to normalized `parties`.

For post-generation attention signaling, Rpy uses one deliberately conservative structural rule: when at least one normalized party has explicit `side=Passive`, the normalized `representatives` collection is empty, and no supplied movement title/text contains an explicit citation marker, the summary must surface a generic attention warning. If any normalized representative exists without an explicit `represents` link, Rpy does **not** infer whether that representative belongs to the passive party and therefore does not emit this warning from that evidence alone.

The raw source payload remains unchanged and can therefore retain the original source value under the repository's existing access, retention and audit controls.

## Movement text

Recoverable movement text is derived independently from the raw source. The current normalization:

- replaces non-breaking spaces and collapses repeated whitespace;
- removes a leading numeric movement prefix from searchable text while preserving source numbering in metadata;
- corrects the literal source artifact `ELETRÔNICAREFER` to `ELETRÔNICA REFER`;
- separates conservative lower-case/upper-case glued word boundaries;
- replaces standalone 11- or 14-digit identifiers with `[documento removido]`.

The internal `step_number` remains deterministic by received movement order. A source-provided number is stored separately as `metadata.source_step_number` when it is a non-negative integer representation.

## Dates

Accepted movement timestamps are normalized to `America/Sao_Paulo`. Explicit offsets are respected; timestamps ending in `Z` are treated as UTC; naive timestamps are conservatively interpreted as UTC. Invalid values remain unavailable instead of being inferred.

The original timestamp is retained as `metadata.source_step_date`, and `metadata.occurred_at_sao_paulo` records the localized ISO-8601 representation. After PostgreSQL `TIMESTAMPTZ` round-trip, the provider serialization converts the instant to `America/Sao_Paulo` again before presentation.

## Provider boundary

The provider context is built from normalized process fields and normalized movement text, not directly from `source_payload`. Tests must prove that full identifiers and external `summary` content do not cross this boundary while the raw callback remains auditable internally.
