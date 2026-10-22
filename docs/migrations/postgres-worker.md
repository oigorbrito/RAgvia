# Migration: PostgreSQL worker core

## Source inspirations

- HydraTask: production-shaped queue lifecycle, worker heartbeat, retry/reclaim patterns.
- Iank314/task-queue: lease/recovery reference.

No donor repository is copied wholesale. Rpy retains only queue/worker mechanics required by the project and adapts them to Rpy's schema, tenancy, lifecycle, tests, and operational constraints.

The Rpy project owner has confirmed direct, explicit authorization from the authors of HydraTask and Iank314/task-queue to use the relevant code and implementation patterns. The repositories did not expose a published license during the pre-v0 provenance audit, so that direct permission is the recorded provenance basis for the relevant transplanted/adapted material. Private permission correspondence is not reproduced in this repository. See the root `NOTICE` file.

## Migration classification

`COPY`, `ADAPT`, `REFERENCE_ONLY`, and `DROP` are migration-strategy labels used by Rpy. `COPY` means the capability/implementation slice was intentionally transplanted under its applicable permission basis; it does not mean that an entire donor file or repository was copied verbatim.

### COPY

- PostgreSQL-backed claim model.
- `FOR UPDATE SKIP LOCKED` concurrency primitive.
- Heartbeat ownership by `worker_id`.
- Retry with bounded exponential backoff.
- Reclaim of jobs whose heartbeat expired.
- Graceful process shutdown.

### ADAPT

- Task registry reduced to an explicit Python mapping/decorator.
- Queue schema reduced to fields required by Rpy.
- Queue behavior integrated with Rpy tenancy, idempotency, lifecycle and audit requirements.
- Scheduler/reclaimer responsibilities separated according to the production topology contract.
- Job payloads/results are JSONB and domain-neutral.

### DROP

- Donor dashboard and UI.
- Donor DAG/dependency product surface.
- Donor-specific event model and metrics stack.
- Donor product naming.
- Infrastructure and abstractions not required by Rpy.

## Required invariants

1. Claim must use `FOR UPDATE SKIP LOCKED`.
2. A processing job is owned by a `worker_id`.
3. Heartbeat/complete/fail operations must verify that owner.
4. Failed jobs retry until `max_attempts`, then become `dead`.
5. A crashed worker must not leave a job permanently stuck in `processing`.
6. Redis, Celery, RabbitMQ and BullMQ are prohibited.

## Current Rpy files

- `app/queue.py`
- `app/db.py`
- `app/tasks.py`
- `app/worker.py`
- `sql/001_init.sql`
- PostgreSQL queue/worker integration tests under `tests/integration/`

## Verification status

The pre-v0 release gate now verifies the worker/queue invariants against PostgreSQL, including concurrent claims, retry/dead transitions, stale-job recovery, ownership checks and the full offline release smoke. The migration harness additionally rejects drift from the required PostgreSQL queue primitives.
