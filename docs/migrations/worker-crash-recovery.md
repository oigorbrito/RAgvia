# Worker crash recovery hardening

## Scope

This block verifies ownership fencing and crash recovery semantics for PostgreSQL-backed jobs.

## Guarantees

- a stale processing job is returned to pending when attempts remain;
- a replacement worker claims the same job with a new `worker_id` and incremented attempt;
- the stale worker can no longer heartbeat, complete, or fail the job after ownership changes;
- only the replacement worker can complete it;
- a crash on the last allowed attempt transitions the job to `dead`;
- completed jobs are outside the reclaimer selection and are not resurrected.

## Reuse policy

This block keeps the generic queue ownership pattern from the donor design, but all tests, naming and runtime contracts are Rpy-specific.
