#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: verify_backup_bundle.sh <backup.dump>" >&2
  exit 2
fi

backup=$1
checksum="$backup.sha256"

if [ ! -f "$backup" ]; then
  echo "backup not found: $backup" >&2
  exit 2
fi

if [ ! -f "$checksum" ]; then
  echo "checksum not found: $checksum" >&2
  exit 2
fi

# Bind the sidecar digest to the archive argument itself. Do not trust the
# filename embedded in the sidecar: a relocated or tampered checksum file could
# otherwise point checksum verification at a different file in the same directory.
sidecar_lines=$(wc -l < "$checksum" | tr -d '[:space:]')
if [ "$sidecar_lines" != "1" ]; then
  echo "checksum sidecar must contain exactly one record: $checksum" >&2
  exit 2
fi

expected_sha256=$(awk 'NR == 1 { print $1 }' "$checksum")
if [ "${#expected_sha256}" -ne 64 ]; then
  echo "checksum sidecar contains an invalid SHA-256 digest: $checksum" >&2
  exit 2
fi
case "$expected_sha256" in
  *[!0-9a-fA-F]*)
    echo "checksum sidecar contains an invalid SHA-256 digest: $checksum" >&2
    exit 2
    ;;
esac

actual_sha256=$(sha256sum "$backup" | awk '{ print $1 }')
if [ "$actual_sha256" != "$expected_sha256" ]; then
  echo "checksum mismatch for backup: $backup" >&2
  exit 1
fi

pg_restore --list "$backup" >/dev/null

backup_name=$(basename "$backup")
echo "backup bundle verified: $backup_name"
