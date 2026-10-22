# Public API v1 contract

The `/v1` surface is the durable public contract for summary requests. Internal PostgreSQL queue job IDs are never exposed as public job identifiers.

## Authentication and authorization

Both legacy Bearer credentials and tenant API keys remain supported during migration. API keys are authenticated, rate-limited, and authorized against the requested CNJ before process data or provider-backed acquisition can run.

`GET /v1/resumos/{job_id}` first resolves the job inside the authenticated tenant, returning `404` for another tenant, then applies the API-key CNJ scope associated with that public job. This prevents job-ID enumeration from becoming a cross-tenant existence oracle.

## Routes

### `POST /v1/resumos`

Creates or replays one public summary request.

Requirements:

- `Authorization: Bearer ...`;
- `Idempotency-Key` header;
- JSON object containing `cnj`;
- optional `format`, either `jsx` (default) or `json`.

The normalized request body is fingerprinted. Reusing the same key and payload returns the same `job_id` without duplicating the Judit acquisition. Reusing the key with a different normalized payload returns `409`.

Response fields include:

- `job_id`;
- `poll_url`;
- `status`;
- `cnj`;
- `source_updated_at`;
- `sources`;
- `usage`;
- `flags`;
- `validation`;
- `format`;
- `iaSummary`;
- `error_code`.

A newly accepted request normally starts as `queued` and returns HTTP `202`.

### `GET /v1/resumos/{job_id}`

Returns the durable public lifecycle and, when available, the validated summary result. The public lifecycle is:

`queued → fetching → indexing → generating → validating → completed`

Terminal states are:

- `failed`;
- `source_unavailable`;
- `secrecy_blocked`;
- `validation_failed`.

Retries and stale-job reclaim cannot move a request backwards from a later or terminal state.

### `GET /v1/processos/{cnj}/resumo`

Returns only the latest tenant-authorized summary for the current process version whose persisted validation has `passed=true`. Invalid generated output is never published through this route.

### `GET /v1/processos/{cnj}/fontes`

Returns authorized, sanitized provenance for the current version. The response deliberately excludes `process_versions.source_payload`, Judit raw payloads, provider credentials, prompts, and raw external request identifiers.

The `sources` list contains:

- a sanitized `judit_lawsuit` version marker (`source_version`, cache flag and finalization timestamp);
- normalized movement provenance (`step_number`, optional `source_step_number`, date and title).

Movement text is not part of this provenance response. Secret processes already have no normalized/indexed movements under the ingestion secrecy boundary.

## Usage and validation

When a valid summary exists, `usage` exposes only local persisted generation metadata (`model`, `prompt_version`, `generation_ms`). Token accounting is not fabricated when it is not persisted.

`validation` is the persisted post-generation validation envelope. `iaSummary` is populated only when that validation passed. The frontend renders the JSX/Markdown string from `iaSummary` when the legacy nested `summary.markdown` field is absent; it deliberately does not interpret structured JSON `iaSummary` as Markdown.

`format=jsx` is the default and returns the deterministic Markdown/allowlisted-JSX document in `iaSummary`. `format=json` returns the persisted structured representation built from the same validated provider payload and normalized process identity. Selecting JSON does not invoke the model again and does not parse the rendered JSX. The JSON representation is versioned with `schema_version`; its top-level fields are `process` and `summary`. Schema version 3 adds the allowlisted `process.representatives` projection. Version 2 documents remain readable as legacy records and may omit that property; new version 3 documents require it (an empty list means no representative was recorded).

The selected format participates in the normalized idempotency fingerprint and is stored with the public request. Polling `GET /v1/resumos/{job_id}` therefore keeps the representation chosen when the job was created. `GET /v1/processos/{cnj}/resumo` also accepts `?format=jsx|json` for representation selection of an already validated summary.

## Health and metrics equivalence

- `/health` and `/healthz` are equivalent liveness probes;
- `/ready` and `/readyz` are equivalent PostgreSQL-backed readiness probes;
- `/ops/metrics` is the existing protected metrics surface and is the documented equivalent of a generic `/metrics` route for this release. It remains guarded by `RPY_OPS_TOKEN` rather than being exposed publicly.

## Provider behavior in tests

API/PostgreSQL contract tests do not require real Judit, Anthropic or OpenAI calls. Creation only enqueues the existing acquisition task; completed-response fixtures use synthetic persisted data or fake/local provider behavior.
