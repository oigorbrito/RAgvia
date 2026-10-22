# Finalized retry retention behavior

`processes.updated_at` drives process expunge eligibility. A duplicate callback for a Judit source version that is already finalized is not new process activity and must not extend that retention clock.

`stage_version()` now separates process identity lookup from activity refresh. It always returns the existing process id, but updates `processes.updated_at` only when the staged version returned by the upsert is not finalized. Therefore:

- a newly staged version refreshes process activity;
- a retry while staging is still open refreshes activity;
- a duplicate of an already-finalized `source_request_id` preserves both source data and the process retention timestamp.

This prevents repeated late deliveries from postponing LGPD/process-retention expunge indefinitely while preserving the existing behavior for genuine new process data.
