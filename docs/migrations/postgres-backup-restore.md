# PostgreSQL backup/restore verification

## Added

- `scripts/backup_database.sh`: custom-format `pg_dump`, restricted file permissions, SHA-256 checksum and archive readability validation.
- `scripts/restore_database.sh`: checksum verification plus guarded `pg_restore --clean --if-exists --exit-on-error`.
- `scripts/verify_backup_restore.sh`: destructive CI drill against a separate restore database.
- `docs/deployment/backup-restore.md`: operator runbook, RPO/RTO boundaries and storage guidance.

## CI invariant

The CI drill restores into `rpy_restore_test`, not over the source test database. It proves restoration of application data, migration metadata and the pgvector extension.

This is intentionally a logical-backup portability layer. Production installations may and should add managed snapshots/PITR where available; those provider-specific mechanisms are outside the application repository.
