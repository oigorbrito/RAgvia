#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required" >&2
  exit 2
fi

if [ "${ALLOW_DESTRUCTIVE_RESTORE:-}" != "YES" ]; then
  echo "refusing restore: set ALLOW_DESTRUCTIVE_RESTORE=YES" >&2
  exit 2
fi

if [ "$#" -ne 1 ]; then
  echo "usage: DATABASE_URL=... ALLOW_DESTRUCTIVE_RESTORE=YES restore_database.sh <backup.dump>" >&2
  exit 2
fi

backup=$1

sh "$(dirname "$0")/verify_backup_bundle.sh" "$backup"

pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  --dbname="$DATABASE_URL" \
  "$backup"

echo "restore completed from: $backup"
