# RAGVIA local PostgreSQL identity migration

This procedure applies only to the Compose project named `ragvia`. The shared
`compose.yaml` also supports RagV/Rpy and homologation installations, so this
identity is selected through `compose.ragvia.yaml`; do not use that overlay for
those projects. The overlay uses Compose `!override` to replace, not append to,
the PostgreSQL mount; require Docker Compose v2.24.4 or later and verify the
effective config before starting services.

## Identity and compatibility

The RAGVIA overlay selects database `ragvia`, login/owner role `ragvia`, and the
explicit Docker volume `ragvia_pgdata`. It overrides the base Compose volume
mount and every service `DATABASE_URL` without changing the shared Compose
contract. The legal pipeline and ordered SQL migrations are unchanged.

For an existing local RAGVIA installation, changing PostgreSQL environment
variables cannot rename a database or rotate an initialized role. Use a custom
format logical backup and restore into the new volume with the same PostgreSQL/
pgvector image digest. Keep the old project volume and its private credential
until the new stack passes acceptance. Never rename or copy PostgreSQL's
internal data directory by hand, and never run both clusters against one volume.

## Starting the RAGVIA Compose project

Supply the existing provider-free local defaults and a private environment file
containing a URL-safe `POSTGRES_PASSWORD`. Do not commit that file. This overlay
marks `ragvia_pgdata` as external: Compose will fail if it is absent rather than
silently create a volume or fall back to the base `rpy_pgdata` mount. Never start
a migrating installation until the restored target volume is ready. For a fresh,
empty local installation only, first confirm the exact volume name is unused,
then create it explicitly:

```sh
docker volume inspect ragvia_pgdata
# Only if inspection confirms it does not exist and this is a fresh installation:
docker volume create ragvia_pgdata
```

Always include `compose.ragvia.yaml` when starting RAGVIA. Omitting it selects
the shared `rpy` database and volume contract instead. The shell environment has
higher interpolation precedence than `--env-file`; unset any inherited legacy
`POSTGRES_PASSWORD` so the private RAGVIA file is authoritative. In POSIX
shells, prefix the Compose command with `env -u POSTGRES_PASSWORD`. In
PowerShell, run `Remove-Item Env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue`
before the command and omit the `env -u` prefix from the snippet.

```sh
env -u POSTGRES_PASSWORD docker compose \
  --project-name ragvia \
  --env-file .env.example \
  --env-file /secure/local/ragvia-postgres.env \
  -f compose.yaml \
  -f compose.ragvia.yaml \
  up -d postgres migrate api scheduler worker-1 worker-2
```

The password must match the `ragvia` role in `ragvia_pgdata`; changing it in the
environment file alone does not rotate the PostgreSQL role password. The
`docker compose config` command renders the resolved password inside service
URLs: treat its full output as secret and do not paste it into logs, tickets, or
CI artifacts. The identity test captures and parses config output using only a
synthetic password.

## Migration and rollback

1. Confirm the active Compose project is `ragvia` and its current PostgreSQL
   container is mounted only on `ragvia_rpy_pgdata`. Confirm `ragvia_pgdata` does
   not already exist or belong to another project.
2. Create a private `pg_dump --format=custom --no-owner` archive and checksum.
   Verify the checksum and that `pg_restore --list` can parse the archive before
   making any migration or cutover changes.
3. Create `ragvia_pgdata` only after confirming that exact volume name is
   unused (`docker volume create ragvia_pgdata`). Start a temporary PostgreSQL
   container using the exact current pgvector/PostgreSQL image digest and private
   RAGVIA credential. Restore the archive into database `ragvia` as role
   `ragvia` using
   `pg_restore --exit-on-error --no-owner --role=ragvia`. Do not pass `--clean`
   and do not alter the source database.
4. Compare schema, per-table data, migration checksums, extensions, and ACLs
   between source and restore. Run the application migration command against
   the restored database and prove it is a no-op with no migration drift.
5. Stop only the RAGVIA API, workers, and scheduler. Take and verify a final
   pre-cutover archive. Confirm the restored snapshot still matches the final
   source; if it does not, restore the final archive into a fresh target database
   before cutover.
6. Stop and remove only the temporary PostgreSQL container (not its volume).
   Recreate the Compose PostgreSQL service with the RAGVIA overlay and confirm
   it mounts `ragvia_pgdata`. Start migrations, then API, scheduler, and workers.
7. Verify `/health`, `/ready`, database identity and role, migration ledger,
   extensions, application-driver read/write, provider-free logs, and a
   controlled database restart.

Rollback before acceptance: stop only the RAGVIA services and start the shared
base Compose **without** `compose.ragvia.yaml`, using the preserved old private
credential and the same project name. The explicit `--project-name ragvia` is
required so Compose reconnects to `ragvia_rpy_pgdata` rather than creating a
second project-scoped volume:

```sh
env -u POSTGRES_PASSWORD docker compose \
  --project-name ragvia \
  --env-file .env.example \
  --env-file /secure/local/ragvia-rpy-postgres.env \
  -f compose.yaml \
  up -d postgres migrate api scheduler worker-1 worker-2
```

This reconnects the project to the unchanged `ragvia_rpy_pgdata` cluster. The
old volume and backup remain separate; do not delete them as part of this
migration. The new volume can be left detached while rollback is active.

## Recorded local drill

The migration was performed from canonical base commit
`7dd6d0d1a834a1e4593fb82134b490a152da1458` in the isolated Compose project
`ragvia`. PostgreSQL used image digest
`sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`.
The source database contained 37 migration-ledger rows and no process, version,
movement, job, or other application rows. Two private custom-format archives
were made and verified before cutover; the final pre-cutover archive was 138,811
bytes with SHA-256
`0779565430c9d84dd3c602400f860f3777729b7a9b27fdc9ded25923b0786d43`.
`pg_restore --list` parsed 270 TOC entries. The archives and credentials remain
outside the repository.

The restored target matched the source's normalized schema dump and every
per-table data hash, retained all 37 migration filenames/checksums, and had the
same extensions (`vector` 0.8.6, `pgcrypto` 1.3, `plpgsql` 1.0), schema ACL,
and absence of non-owner object ACLs. The source had 33 application tables and
37 rows, all in `schema_migrations`; the target had the same. The application
migration command exited 0 without applying new migrations. A temporary durable
probe written through the API container survived a PostgreSQL restart and was
dropped afterward; the schema and data hashes then matched the source again.
The old volume and old private credential remain available for rollback.
