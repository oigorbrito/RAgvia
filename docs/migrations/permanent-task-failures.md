# Permanent task failure semantics

The durable queue now distinguishes deterministic contract failures from retryable execution failures.

`PermanentTaskError` means repeating the same job payload against the same application state cannot succeed merely by waiting and retrying. The worker records the error through the existing fenced `fail()` path but marks the job `dead` immediately, even when `max_attempts` has not been reached.

All other exceptions retain the existing retry/backoff behavior. Worker crashes and stale-heartbeat recovery are unchanged.

The first production use is the provider prompt hard cap: an oversized final prompt is deterministic and is rejected before any Anthropic call, so retrying it would only consume queue capacity. Provider connection/timeouts and retryable HTTP failures are not converted to `PermanentTaskError` and therefore retain their provider-level and queue-level resilience.

Permanent classification should remain explicit and narrow. Do not wrap arbitrary validation, database, network or provider exceptions merely to reduce retries; only deterministic application contract failures belong in this category.
