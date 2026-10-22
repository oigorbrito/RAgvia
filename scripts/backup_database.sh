#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required" >&2
  exit 2
fi

if [ "$#" -ne 1 ]; then
  echo "usage: DATABASE_URL=... backup_database.sh <output.dump>" >&2
  exit 2
fi

output=$1
output_dir=$(dirname "$output")
output_name=$(basename "$output")
mkdir -p "$output_dir"

umask 077
pg_dump \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-privileges \
  --file="$output" \
  "$DATABASE_URL"

# Store only the archive basename in the sidecar. The dump/checksum pair can then
# be copied to a different directory or host and still pass sha256sum -c there.
(
  cd "$output_dir"
  sha256sum "$output_name" > "$output_name.sha256"
)

# Validate that the custom archive can be read before considering it a backup.
pg_restore --list "$output" >/dev/null

archive_name=$output_name
archive_bytes=$(wc -c < "$output" | tr -d ' ')
archive_sha256=$(cut -d ' ' -f1 "$output.sha256")

# Persist only successful, readable backups. The row is deliberately written after
# pg_restore --list so backup age never advances for a corrupt/incomplete archive.
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -v archive_name="$archive_name" \
  -v archive_bytes="$archive_bytes" \
  -v archive_sha256="$archive_sha256" <<'SQL'
INSERT INTO backup_runs (archive_name, archive_bytes, sha256)
VALUES (:'archive_name', :'archive_bytes'::bigint, :'archive_sha256');
SQL

echo "backup created: $output"
echo "checksum: $output.sha256"
