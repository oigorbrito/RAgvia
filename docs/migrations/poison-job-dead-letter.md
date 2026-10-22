# Invalid job dead-letter contract

A claimed queue row can be invalid before its task handler starts: the task name may not be registered or the persisted payload may not decode to a JSON object. Those are deterministic queue-contract failures, not transient runtime failures.

Workers now resolve the task and decode the payload inside the same failure envelope used by normal job execution. Unknown tasks and malformed payloads are converted to `PermanentTaskError`, sanitized, persisted through the existing fenced `fail()` path, and moved directly to `dead` on the first attempt.

No heartbeat is started for an invalid job because there is no handler to execute. The worker slot remains alive and can immediately claim the next job. Transient exceptions raised by a valid handler retain the existing retry/backoff semantics, and cancellation still propagates normally.
