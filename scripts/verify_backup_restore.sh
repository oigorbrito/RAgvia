#!/bin/sh
set -eu

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL is required}"
: "${TEST_ADMIN_DATABASE_URL:?TEST_ADMIN_DATABASE_URL is required}"
: "${TEST_RESTORE_DATABASE_URL:?TEST_RESTORE_DATABASE_URL is required}"

PG_CLIENT_IMAGE=${PG_CLIENT_IMAGE:-pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b}
workdir=.tmp/backup-restore
backup="$workdir/source/rpy.dump"
relocated_backup="$workdir/off-host-copy/rpy.dump"
restore_db=rpy_restore_test

rm -rf "$workdir"
mkdir -p "$(dirname "$backup")" "$(dirname "$relocated_backup")"

run_client() {
  # Keep stdin attached so here-doc SQL is actually delivered to psql.
  docker run --rm --interactive --network host "$PG_CLIENT_IMAGE" "$@"
}

cleanup() {
  run_client psql "$TEST_ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS $restore_db WITH (FORCE);" >/dev/null 2>&1 || true
  rm -rf "$workdir"
}
trap cleanup EXIT INT TERM

python scripts/migrate.py --database-url "$TEST_DATABASE_URL"

run_client psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS backup_restore_probe;
CREATE TABLE backup_restore_probe (
  id integer PRIMARY KEY,
  value text NOT NULL
);
INSERT INTO backup_restore_probe (id, value) VALUES (1, 'rpy-backup-restore-ok');
SQL

source_probe=$(run_client psql "$TEST_DATABASE_URL" -Atc \
  "SELECT value FROM backup_restore_probe WHERE id = 1;")
[ "$source_probe" = "rpy-backup-restore-ok" ] || {
  echo "restore drill setup failed: sentinel row missing from source" >&2
  exit 1
}
source_migrations=$(run_client psql "$TEST_DATABASE_URL" -Atc \
  "SELECT count(*) FROM schema_migrations;")

docker run --rm --network host \
  -e DATABASE_URL="$TEST_DATABASE_URL" \
  -v "$PWD:/work" -w /work \
  "$PG_CLIENT_IMAGE" \
  sh scripts/backup_database.sh "$backup"

# The backup container writes with umask 077 as its own uid. Perform the simulated
# off-host copy through the same client image rather than weakening backup modes.
docker run --rm \
  -v "$PWD:/work" -w /work \
  "$PG_CLIENT_IMAGE" \
  sh -c 'cp "$1" "$2" && cp "$1.sha256" "$2.sha256" && rm -rf "$(dirname "$1")"' \
  sh "$backup" "$relocated_backup"

docker run --rm \
  -v "$PWD:/work" -w /work \
  "$PG_CLIENT_IMAGE" \
  sh scripts/verify_backup_bundle.sh "$relocated_backup"

docker run --rm \
  -v "$PWD:/work" -w /work \
  "$PG_CLIENT_IMAGE" \
  pg_restore --list "$relocated_backup" | grep -q "backup_restore_probe" || {
    echo "restore drill failed: sentinel table missing from backup TOC" >&2
    exit 1
  }

run_client psql "$TEST_ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $restore_db WITH (FORCE);" \
  -c "CREATE DATABASE $restore_db;"

docker run --rm --network host \
  -e DATABASE_URL="$TEST_RESTORE_DATABASE_URL" \
  -e ALLOW_DESTRUCTIVE_RESTORE=YES \
  -v "$PWD:/work" -w /work \
  "$PG_CLIENT_IMAGE" \
  sh scripts/restore_database.sh "$relocated_backup"

restored_probe=$(run_client psql "$TEST_RESTORE_DATABASE_URL" -Atc \
  "SELECT value FROM backup_restore_probe WHERE id = 1;")
restored_migrations=$(run_client psql "$TEST_RESTORE_DATABASE_URL" -Atc \
  "SELECT count(*) FROM schema_migrations;")
restored_vector=$(run_client psql "$TEST_RESTORE_DATABASE_URL" -Atc \
  "SELECT count(*) FROM pg_extension WHERE extname = 'vector';")

[ "$restored_probe" = "rpy-backup-restore-ok" ] || {
  echo "restore drill failed: sentinel row mismatch" >&2
  exit 1
}
[ "$restored_migrations" = "$source_migrations" ] || {
  echo "restore drill failed: schema_migrations count mismatch" >&2
  exit 1
}
[ "$restored_vector" = "1" ] || {
  echo "restore drill failed: pgvector extension missing" >&2
  exit 1
}

echo "backup/restore drill: ok"
