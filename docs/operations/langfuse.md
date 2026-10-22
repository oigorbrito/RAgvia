# Langfuse self-hosted observability

Rpy treats Langfuse as an optional, fail-open observability sink. PostgreSQL remains the durable product source of truth and Langfuse availability must never decide whether a summary is generated, validated, persisted, or published.

## Runtime contract

The production image includes the constrained `rpy[observability]` extra and the image smoke verifies that the Langfuse SDK is importable. Tracing is still disabled by default and must be enabled explicitly with `LANGFUSE_ENABLED=true`.

Production activation requires worker-only:

- `LANGFUSE_PUBLIC_KEY`;
- `LANGFUSE_SECRET_KEY`;
- `LANGFUSE_BASE_URL` using HTTPS;
- `LANGFUSE_TRACING_ENVIRONMENT` (default `production`).

The official Python SDK v4 uses these environment variables with `get_client()`. The tracing environment must satisfy the Langfuse naming contract (lowercase letters/numbers/hyphens/underscores, maximum 40 characters, and no `langfuse` prefix). Production preflight validates this shape before deployment.

The integration targets the Langfuse Python SDK v4/OpenTelemetry observation API. It does not use the legacy trace/span/generation ingestion APIs. If configuration is disabled or the client raises, the worker continues without tracing.

## Data minimization

The trace boundary is an allowlist. Rpy may export only:

- queue `job_id`;
- internal `process_id` and `version_id`;
- Judit request identifier used for pipeline correlation;
- persisted summary identifier;
- model and prompt version when available;
- generation latency;
- provider-reported token/cache usage and provider-reported cost;
- cache hit, persistence/reuse and validation pass/fail flags;
- count of validation errors, never the validation messages;
- identifiers of movement sources persisted in `process_summary_sources`.

Rpy must not export process text, movement text, parties, raw Judit/DataJud payloads, provider prompts, provider responses, generated Markdown, validation error strings, credentials, tokens, attachment contents, or other document content. Secret processes use the same metadata-only boundary; restricted content is never added to the trace.

## Retention and access

Langfuse traces are operational telemetry, not the legal/audit record. Production deployments must:

1. restrict Langfuse access to the operations/engineering roles that already have production observability access;
2. disable public trace sharing;
3. configure an explicit trace-retention policy appropriate for incident response and performance analysis; Rpy recommends **30 days** unless the data-protection owner approves a different period;
4. verify that the selected Langfuse deployment/edition actually supports the intended retention policy;
5. keep PostgreSQL audit/provenance retention independent from Langfuse retention;
6. rotate Langfuse credentials independently from application/provider credentials.

Self-hosted Langfuse does not automatically expire event data by default. A 30-day Rpy target is therefore an operational policy to configure and verify, not an SDK default.

A deployment that cannot enforce access control and retention must leave `LANGFUSE_ENABLED=false`.

## Failure behavior

Start, update and end failures at the Langfuse boundary are swallowed after a content-free warning containing only the exception type. The queue job continues with its normal success/failure semantics. Tests must use fake clients and must not make network calls or require Langfuse credentials.
