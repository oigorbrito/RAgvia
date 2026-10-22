# Graceful worker drain

Workers now treat process shutdown as a drain transition rather than an immediate cancellation of every slot.

On SIGTERM/SIGINT the worker stops accepting new jobs and stops its stale-job reclaimer. Already-claimed jobs keep their heartbeat alive and may finish for `WORKER_SHUTDOWN_GRACE_SECONDS` (default 30 seconds). When that window expires, remaining slot tasks are cancelled; their jobs remain fenced by `worker_id` and are recovered by the normal stale-heartbeat reclaim path in another worker.

Production Compose sets `stop_grace_period` separately (default 40 seconds). The production validator requires the container grace to be greater than the application drain window and requires both worker replicas to use the same shutdown contract. This prevents the container runtime from sending SIGKILL before the application-level drain completes.

The drain window is intentionally lower than the normal task timeout. Deploy shutdown should favor completing short in-flight work without allowing a hung provider call to block container replacement indefinitely.
