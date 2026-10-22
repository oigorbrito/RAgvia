# Operational observability

Rpy exposes aggregate operational signals at `GET /ops/metrics`. The endpoint is hidden behind `RPY_OPS_TOKEN` and returns no process identifiers, CNJs, parties, summary text, webhook payloads or provider request bodies.

`/health` remains a process liveness probe. `/ready` remains a PostgreSQL reachability probe. Operational SLO breaches do not make the API unready automatically; they are intended for alerting and operator action.

## Durable signals

The endpoint reports:

- queue counts by status;
- age of the oldest runnable pending job;
- processing jobs with missing/stale heartbeat;
- dead jobs in the last 24 hours, including aggregate counts by task name;
- summary validation rate plus average and p95 generation latency;
- Judit callback throughput in the last hour and time since the last persisted callback;
- age and timestamp of the last verified database backup;
- an aggregate `operational_health.status` of `ok`, `degraded` or `critical`, plus machine-readable alerts.

Backup recency comes from `backup_runs`. `scripts/backup_database.sh` writes this row only after `pg_dump` succeeds, SHA-256 is written, and `pg_restore --list` confirms the archive is readable. A failed or corrupt backup therefore cannot advance the backup-age signal.

## Default thresholds

- backup older than 26 hours: critical;
- runnable queue lag >= 5 minutes: degraded;
- runnable queue lag >= 30 minutes: critical;
- processing heartbeat older than 60 seconds (or missing): critical;
- >= 1 dead job in 24 hours: degraded;
- >= 5 dead jobs in 24 hours: critical;
- summary generation p95 >= 60 seconds: degraded;
- summary generation p95 >= 120 seconds: critical.

The environment variables are documented in `.env.production.example`. Invalid or inverted threshold configuration fails API startup instead of silently disabling alerts.

## Alerting contract

A production monitor should authenticate to `/ops/metrics` over the private/ingress path and alert on `operational_health.status`. Keep the raw endpoint private: `RPY_OPS_TOKEN` is an operations credential and must not be exposed to clients.

Suggested routing:

- `critical`: page/on-call notification;
- `degraded`: ticket/chat notification with a bounded response window;
- `ok`: no notification.

Webhook inactivity is reported but is not itself classified as unhealthy because expected callback volume depends on workload. An external monitor can add a workload-specific inactivity rule when the normal arrival rate is known.

Generation latency is an end-to-end summary-generation signal and therefore includes provider latency plus local prompt/validation/persistence work. Provider-specific request telemetry can be added later if needed without sending prompt or case content to the metrics surface.
