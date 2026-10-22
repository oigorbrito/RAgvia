# Migration safety guard

Rpy treats production schema changes as part of the deploy/rollback contract. The repository therefore checks SQL migrations for operations that can break an older application image during rollback or mixed-version deployment.

The guard currently flags:

- `DROP TABLE`, `DROP COLUMN`, `DROP TYPE`, `DROP SCHEMA`, `DROP EXTENSION`;
- `TRUNCATE`;
- direct `ALTER COLUMN ... TYPE`;
- direct `ALTER COLUMN ... SET NOT NULL`;
- table/type renames.

The preferred pattern is expand/migrate/contract: first add compatible schema, deploy code that can work with both shapes, migrate/backfill data, and only remove or rename old schema after the compatibility window has closed.

## Intentional destructive changes

A destructive migration is not impossible, but it must be explicit. The SQL file must contain:

`-- migration-safety: allow-destructive`

and a matching `docs/migrations/<migration-stem>.md` must contain the heading:

`## Destructive migration approval`

That document should record why the destructive operation is required, which application versions are compatible, how data is backed up or migrated, the rollback consequence, and the deployment ordering. A waiver marker without a detected destructive operation is rejected so stale approvals do not remain attached to later edits.

The unit suite scans every repository migration, so the existing CI enforces this guard without requiring a separate workflow permission or action.
