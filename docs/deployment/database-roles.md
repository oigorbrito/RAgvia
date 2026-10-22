# Production database roles

Production database access is split by responsibility. Runtime services do not receive the migration/admin credential.

## Roles

- `MIGRATION_DATABASE_URL`: one-shot deploy credential. It applies schema migrations and provisions/rotates the runtime roles after the schema is current. This credential is not injected into API, workers or scheduler.
- `API_DATABASE_URL` / `rpy_api`: reads authorized process state and operational aggregates; stages Judit versions; enqueues work; inserts immutable access-log entries. It cannot delete processes or create/alter schema objects.
- `WORKER_DATABASE_URL` / `rpy_worker`: claims/updates jobs, finalizes process data, manages process steps and persists summaries. It cannot delete processes or create schema objects.
- `SCHEDULER_DATABASE_URL` / `rpy_scheduler`: reads the identifiers needed for retention and deletes expired processes, terminal jobs and associated Judit delivery rows. It cannot read summaries or mutate ingestion/generation data directly.
- `BACKUP_DATABASE_URL` / `rpy_backup`: read-only access across application tables/sequences plus `INSERT` on `backup_runs`, so a verified dump can record completion without receiving general write access.

All runtime roles are forced to `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`. `CREATE` on the `public` schema is revoked from `PUBLIC`, and runtime roles receive only `USAGE` on that schema.

## Provisioning

The production `migrate` service runs the migrations first and then executes `scripts/provision_db_roles.py`. The provisioning step reads the four runtime URLs, validates the expected role names, creates or rotates those login roles and reapplies the explicit grants.

The provisioning credential therefore needs enough authority to create/alter roles and grant privileges on the application objects. On self-hosted Compose this is normally the PostgreSQL administrative deployment credential. On managed PostgreSQL, use the provider's deployment/admin role or pre-provision the roles and run the grant step with an account that has equivalent authority.

Do not reuse one runtime password across roles. URL-encode reserved characters in passwords. Rotation is performed by changing the affected URL secret and rerunning the one-shot migrate/provision step before restarting that runtime service.

## New tables and permissions

The backup role receives read access to future tables/sequences through default privileges owned by the migration role. API/worker/scheduler grants for a new table are intentionally **not** automatic: every new runtime capability must add an explicit grant in `scripts/provision_db_roles.py` and an integration assertion. This prevents a new table from silently becoming writable by every runtime component.

## Backup invocation

Operational backup jobs should invoke `scripts/backup_database.sh` with the backup credential:

```sh
DATABASE_URL="$BACKUP_DATABASE_URL" sh scripts/backup_database.sh /secure/path/rpy.dump
```

`rpy_backup` can read the database and insert the verified completion record in `backup_runs`; it cannot update or delete domain rows.
