# Bounded job error history

`jobs.error_log` is durable operational history, not an unbounded event store. Repeated task failures and heartbeat reclaims now retain at most 16,000 characters per job.

Both normal `fail()` transitions and stale-worker reclaim use the same bound and preserve the newest tail of the history. This keeps the most relevant diagnostics while preventing pathological growth when a caller configures a large `max_attempts` value or a job repeatedly crashes and is reclaimed.

This bound complements worker-side message redaction and per-error truncation. New queue failure paths must use the same bounded storage contract rather than appending directly to `jobs.error_log`.
