# Judit delivery retention

`judit_deliveries.raw_payload` stores the webhook envelope needed for short-term delivery deduplication and operational diagnosis. It is not the canonical process store: staged lawsuit data lives in `process_versions`, and completion recovery state lives in `judit_request_completions`.

The singleton scheduler now deletes `judit_deliveries` rows older than `JOB_RETENTION_DAYS` (30 days by default), using `received_at` as the cutoff. This keeps raw webhook payload retention bounded even for completion or informational callbacks that are not tied to a process later expunged by the longer process-retention policy.

Recent deliveries remain available for retry deduplication. Removing an old delivery marker can allow an extremely late callback to be processed again, but downstream process-version uniqueness, finalize idempotency, summary idempotency, and monotonic promotion remain the durable correctness boundaries.

No new database privilege is required: the scheduler role already has `DELETE` on `judit_deliveries`.
