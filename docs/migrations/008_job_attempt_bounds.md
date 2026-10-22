# Queue attempt bounds

New queue jobs are limited to `max_attempts` between 1 and 100. The application validates this range before enqueue, and PostgreSQL enforces the same rule for direct writes.

Migration `008_job_attempt_bounds.sql` adds the database check as `NOT VALID`. PostgreSQL still enforces a NOT VALID check constraint for new inserts and updates, while skipping the full historical-table validation scan during deployment. Existing historical rows outside the range, if any, are not rewritten or silently changed by this migration.

Operators can audit historical exceptions with:

```sql
SELECT id, task_name, status, attempts, max_attempts
FROM jobs
WHERE max_attempts NOT BETWEEN 1 AND 100;
```

If that query is empty after deployment, the constraint may be validated in a later migration with `ALTER TABLE jobs VALIDATE CONSTRAINT jobs_max_attempts_bounds_chk;`.
