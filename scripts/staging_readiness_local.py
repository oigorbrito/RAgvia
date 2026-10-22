from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ProbeResult:
    path: str
    status: int


def _base_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("base URL must be a bare HTTPS origin")
    return f"https://{parsed.netloc}"


def _request(origin: str, path: str, *, method: str = "GET") -> ProbeResult:
    request = urllib.request.Request(
        f"{origin}{path}",
        data=(b"{}" if method == "POST" else None),
        method=method,
        headers={"Content-Type": "application/json"} if method == "POST" else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = int(response.status)
            if method == "GET":
                body = response.read()
                if body:
                    json.loads(body)
            return ProbeResult(path=path, status=status)
    except urllib.error.HTTPError as exc:
        return ProbeResult(path=path, status=int(exc.code))


def run_probe(base_url: str) -> list[ProbeResult]:
    origin = _base_origin(base_url)
    results = [
        _request(origin, "/health"),
        _request(origin, "/ready"),
        _request(
            origin,
            "/webhooks/judit/rpy-readiness-invalid-token",
            method="POST",
        ),
    ]
    expected = {
        "/health": 200,
        "/ready": 200,
        "/webhooks/judit/rpy-readiness-invalid-token": 404,
    }
    failures = [
        f"{result.path}: expected {expected[result.path]}, got {result.status}"
        for result in results
        if result.status != expected[result.path]
    ]
    if failures:
        raise RuntimeError("; ".join(failures))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Provider-free local staging readiness probe. "
            "Checks HTTPS health/readiness and an invalid Judit webhook route."
        )
    )
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    try:
        results = run_probe(args.base_url)
    except (ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"staging readiness: BLOCKED ({type(exc).__name__})")
        return 1

    print("staging readiness: PASS")
    for result in results:
        print(f" - {result.path}: HTTP {result.status}")
    print(" - paid provider requests: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
