#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: verify_image_runtime.sh <image-ref>" >&2
  exit 2
fi

image=$1

# Python startup itself must be clean. A stale .pth file can emit a site
# initialization traceback and still return exit code 0, so capture stderr
# explicitly before exercising application imports.
startup_output=$(docker run --rm --entrypoint python "$image" -c 'pass' 2>&1)
if [ -n "$startup_output" ]; then
  echo "python runtime emitted unexpected startup output:" >&2
  echo "$startup_output" >&2
  exit 1
fi

# Exercise the exact runtime artifact, not the checkout. This catches missing
# packaged/static files and accidental root execution before publication.
docker run --rm --entrypoint python "$image" - <<'PY'
import importlib.util
import os
from pathlib import Path

import app.api
import app.worker
import app.scheduler
from app.migrations import resolve_migrations
import langfuse

frontend = Path(app.api.__file__).with_name("frontend")
required = [frontend / "index.html", frontend / "app.css", frontend / "app.js"]
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"frontend assets missing from image: {', '.join(missing)}")

if os.getuid() == 0:
    raise SystemExit("image runtime user must not be root")

migration_dir, migrations = resolve_migrations()
if migration_dir != Path("/app/sql"):
    raise SystemExit(f"runtime migration directory mismatch: {migration_dir}")
if not migrations or migrations[0].name != "001_init.sql":
    raise SystemExit("runtime migration set is missing 001_init.sql")

for package_manager in ("pip", "setuptools"):
    if importlib.util.find_spec(package_manager) is not None:
        raise SystemExit(f"{package_manager} must not ship in the production runtime image")
PY

echo "image runtime contract: ok"
