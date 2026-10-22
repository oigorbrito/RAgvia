# Judit completion marker retention

`judit_request_completions` exists only to bridge out-of-order callbacks. It must not become an unbounded historical event store.

The scheduler now deletes completion markers older than `JOB_RETENTION_DAYS`, using the same operational retention window as terminal queue jobs. This keeps the recovery window aligned with the period in which completed/dead queue state is retained for diagnosis.

The purge only targets `judit_request_completions`. It does not delete lawsuit versions, summaries, access audit records, or active queue jobs.
