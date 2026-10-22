from __future__ import annotations

import argparse
import re
from pathlib import Path

DOCKER_RESOLVER_RE = re.compile(
    r"\bresolver\s+127\.0\.0\.11(?:\s+[^;]+)?;",
    re.IGNORECASE,
)
DYNAMIC_UPSTREAM_RE = re.compile(
    r"\bset\s+\$rpy_api\s+[\"']?api:8000[\"']?\s*;",
    re.IGNORECASE,
)
PROXY_PASS_RE = re.compile(
    r"\bproxy_pass\s+http://\$rpy_api\$request_uri\s*;",
    re.IGNORECASE,
)
STATIC_CONTAINER_IP_RE = re.compile(
    r"\bproxy_pass\s+https?://(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+",
    re.IGNORECASE,
)


def validate_staging_ingress(text: str) -> list[str]:
    errors: list[str] = []
    if not DOCKER_RESOLVER_RE.search(text):
        errors.append("Docker DNS resolver 127.0.0.11 is required")
    if not DYNAMIC_UPSTREAM_RE.search(text):
        errors.append("dynamic $rpy_api upstream must target Compose service api:8000")
    if not PROXY_PASS_RE.search(text):
        errors.append("proxy_pass must use the dynamic $rpy_api upstream and preserve request_uri")
    if STATIC_CONTAINER_IP_RE.search(text):
        errors.append("static private/container IP upstreams are prohibited")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the persistent-staging ingress Docker-DNS contract."
    )
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    text = args.config.read_text(encoding="utf-8")
    errors = validate_staging_ingress(text)
    if errors:
        print("staging ingress: BLOCKED")
        for error in errors:
            print(f" - {error}")
        return 1
    print("staging ingress: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
