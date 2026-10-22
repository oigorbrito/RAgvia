# Judit continuous tracking

Rpy uses Judit tracking as a tenant-scoped monitoring layer over the existing PostgreSQL job queue and Judit ingestion pipeline. Tracking does not introduce a second queue or bypass version/finalization rules.

## Runtime contract

Creating a tracking records one `judit_trackings` row per tenant/CNJ, enqueues `create_judit_tracking`, and reuses the existing tenant/CNJ acquisition flow for the initial lawsuit request. Batch creation accepts up to 100 CNJs and deduplicates repeated canonical CNJs before enqueueing work.

Provider I/O runs only in workers. Production workers therefore require `JUDIT_API_KEY`; the API and scheduler do not receive that provider credential. The scheduler remains on the backend-only network and only enqueues durable refresh jobs.

Judit callbacks with `reference_type=tracking` are staged through the same `process_versions` path used by ordinary Judit responses. The provider `reference_id` is matched to the stored `provider_tracking_id`, which updates `last_event_at` and grants the resulting process only to the owning tenant. Repeated provider responses are deduplicated by the existing stable response/version identity.

## Reconciliation

The scheduler calls `enqueue_due_tracking_reconciliations`. A tracking is eligible only after the configured stale interval has elapsed since the later of its last provider event, last reconciliation, or creation time. The scheduler locks candidates with `FOR UPDATE SKIP LOCKED`, creates at most one open refresh row, records `last_reconciled_at`, and enqueues `refresh_judit_tracking`.

Production settings:

- `JUDIT_TRACKING_STALE_SECONDS` defaults to 129600 seconds (36 hours).
- `JUDIT_TRACKING_RECONCILE_LIMIT` defaults to 100 rows per scheduler pass.
- `JUDIT_API_KEY` is supplied to workers only.
- `JUDIT_TIMEOUT_SECONDS` is supplied to workers and remains bounded by the client contract.

A completed Judit request closes the matching open tracking refresh transactionally. Ambiguous provider/network failures are not retried blindly because the provider may already have accepted a paid asynchronous request.

## HTTP API and authorization

The API exposes:

- `POST /v1/trackings/{cnj}` to create one monitoring record;
- `POST /v1/trackings` for a tenant batch;
- `GET /v1/trackings` to list visible monitoring records;
- `DELETE /v1/trackings/{tracking_id}` to remove one monitoring record.

Legacy bearer tenants remain tenant-scoped. API keys must also be authorized for the CNJ through explicit CNJ scope or the existing portfolio scope. List responses are filtered to the CNJs visible to the API key, and cross-tenant deletion returns 404.

All operations are audited without storing provider credentials or raw process payloads in access-log metadata.
