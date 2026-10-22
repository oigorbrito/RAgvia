"""Run integration tests against an owned, disposable PostgreSQL (never a supplied URL)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

import asyncpg

IMAGE = "pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b"
ROOT = Path(__file__).resolve().parents[1]


async def wait_for_sql(url: str, *, timeout: float = 90) -> None:
    # pg_isready on the internal Unix socket can see the temporary initdb server.
    # Probe the published TCP route and credentials that pytest will actually use.
    async with asyncio.timeout(timeout):
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(url, timeout=3)
                if await conn.fetchval("SELECT 1") == 1:
                    return
            except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
                pass
            finally:
                if conn is not None:
                    await conn.close()
            await asyncio.sleep(0.25)


def check_results(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    if totals["tests"] == 0 or any(totals[k] for k in ("failures", "errors", "skipped")):
        raise RuntimeError(f"Integration qualification rejected: {totals}")
    return totals


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New evidence directory; existing directories are rejected")
    parser.add_argument("--test-path", action="append", default=[],
                        help="Repeatable integration file/directory selection; default: all integration tests")
    args = parser.parse_args()
    targets = args.test_path or ["tests/integration"]
    for target in targets:
        resolved = (ROOT / target).resolve()
        if not resolved.is_relative_to(ROOT / "tests/integration") or not resolved.exists():
            parser.error("--test-path must select an existing path under tests/integration")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    name = f"ragvia-qualification-{uuid4().hex[:12]}"
    metadata = {
        "run_id": name, "image": IMAGE,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at_epoch": time.time(),
        "command": [sys.executable, "-m", "pytest", *targets, "-ra", "--tb=long"],
        "scope": "Synthetic integration evidence; not live-provider quality or performance",
    }
    created = False
    try:
        docker("create", "--name", name, "--restart=no", "--label", "ragvia.validation=disposable",
               "--tmpfs", "/var/lib/postgresql/data:rw", "-p", "127.0.0.1::5432",
               "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=rpy_test", IMAGE)
        created = True
        docker("start", name)
        info = json.loads(docker("inspect", name))[0]
        binding = info["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        base = f"postgresql://postgres:postgres@127.0.0.1:{binding['HostPort']}/"
        asyncio.run(wait_for_sql(base + "rpy_test"))
        env = dict(os.environ, TEST_DATABASE_URL=base + "rpy_test",
                   TEST_ADMIN_DATABASE_URL=base + "postgres",
                   TEST_RESTORE_DATABASE_URL=base + "rpy_restore_test",
                   DATABASE_URL=base + "rpy_test", PYTHONPATH=str(ROOT))
        xml = out / "postgres.xml"
        with (out / "postgres.log").open("w", encoding="utf-8") as log:
            result = subprocess.run([*metadata["command"], f"--junitxml={xml}"],
                                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        metadata["pytest_exit_code"] = result.returncode
        metadata["results"] = check_results(xml)
        if result.returncode:
            raise RuntimeError(f"pytest exited {result.returncode}")
        metadata["status"] = "PASS"
        print(json.dumps(metadata["results"]))
        return 0
    except Exception as exc:
        metadata["status"] = "FAIL"
        metadata["error_type"] = type(exc).__name__
        raise
    finally:
        if created:
            try:
                (out / "container.json").write_text(docker("inspect", name), encoding="utf-8")
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                (out / "server.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
            finally:
                docker("rm", "-f", name)
        (out / "run.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
