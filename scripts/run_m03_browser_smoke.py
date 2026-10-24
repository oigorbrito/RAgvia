from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BROWSER_SMOKE = ROOT / "tests/frontend_browser_smoke.py"


def _wait_for_api(origin: str, process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 45
    last_error: Exception | None = None
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with urllib.request.urlopen(f"{origin}/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"local API did not become ready: {last_error}")


def run(api_response_path: Path, browser_auth_path: Path) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TEST_DATABASE_URL must point to the disposable PostgreSQL database")
    response = json.loads(api_response_path.read_text(encoding="utf-8"))
    auth = json.loads(browser_auth_path.read_text(encoding="utf-8"))
    token = str(auth["token"])
    tenant_id = str(auth["tenant_id"])
    if auth.get("code") != response.get("code"):
        raise ValueError("browser auth artifact does not target the canonical API response")

    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "RPY_BEARER_TOKENS": json.dumps({token: tenant_id}),
            "JUDIT_WEBHOOK_TENANT_ID": tenant_id,
            "JUDIT_WEBHOOK_TOKEN": "m03-local-browser-webhook-synthetic",
            "RPY_OPS_TOKEN": "m03-local-browser-ops-synthetic",
            "DATAJUD_ENABLED": "false",
            "ANTHROPIC_API_KEY": "",
        }
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=environment,
    )
    try:
        _wait_for_api(origin, process)
        subprocess.run(
            [
                sys.executable,
                str(BROWSER_SMOKE),
                "--api-response", str(api_response_path),
                "--api-origin", origin,
                "--api-token", token,
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            timeout=180,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-response", type=Path, required=True)
    parser.add_argument("--browser-auth", type=Path, required=True)
    args = parser.parse_args()
    run(args.api_response, args.browser_auth)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
