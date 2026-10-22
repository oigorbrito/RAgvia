# Finalized Judit source immutability

A staged `process_versions` row may be refreshed while `finalized = FALSE`, because retries can deliver a more complete copy of the same Judit response before the finalizer consumes it.

Once `finalized = TRUE`, the source identity and raw source payload become historical input to derived `process_steps` and `process_summaries`. Mutating those source fields afterward would make the durable source disagree with already-produced derived data.

Migration `011_finalized_version_immutability.sql` adds a PostgreSQL trigger that rejects changes to the following fields after finalization:

- `source_request_id`
- `source_cached_response`
- `source_payload`
- `judit_request_id`
- `judit_response_id`
- `judit_callback_id`

`stage_version()` mirrors the same rule in its conflict path: duplicate callbacks for an already-finalized version return the existing version id while preserving the original source fields. Updates to `finalized`/`finalized_at` used by the finalizer remain permitted.
