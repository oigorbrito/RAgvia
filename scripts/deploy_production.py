from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
COMPOSE_FILE = ROOT / "compose.production.yaml"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_deploy_env as deploy_env  # noqa: E402
import validate_production_compose as compose_contract  # noqa: E402

_CONTROLLED_PREFIXES = (
    "RPY_",
    "POSTGRES_",
    "MIGRATION_",
    "WORKER_",
    "SCHEDULER_",
    "BACKUP_",
    "ANTHROPIC_",
    "OPENAI_",
    "JUDIT_",
    "DATAJUD_",
    "LANGFUSE_",
    "EGRESS_",
    "EMBEDDING_",
    "BGE_",
    "ALLOW_",
    "COHERE_",
    "RERANKER_",
    "PROVIDER_",
    "ATTACHMENT_",
    "RETENTION_",
    "JOB_RETENTION_",
    "EXPUNGE_",
    "TRACKING_",
    "OPS_",
)
_CONTROLLED_EXACT = {
    "API_DATABASE_URL",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "HF_HOME",
    "XDG_CACHE_HOME",
}


class DeployError(RuntimeError):
    """Safe deployment failure without rendering secret values."""


def _inside_repo(path: Path) -> bool:
    try:
        path.relative_to(ROOT)
    except ValueError:
        return False
    return True


def _validate_secret_file(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise DeployError("deployment env file does not exist")
    if _inside_repo(resolved):
        raise DeployError("deployment env file must live outside the repository")
    if os.name == "posix":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise DeployError(
                "deployment env file must not be accessible by group or other users"
            )
    return resolved


def _load_validated_values(path: Path) -> dict[str, str]:
    try:
        values = deploy_env._load_env_file(path)
    except ValueError as exc:
        raise DeployError(f"deployment env file is invalid: {exc}") from None
    errors = deploy_env.validate(values)
    if errors:
        rendered = "\n".join(f" - {error}" for error in errors)
        raise DeployError(f"deployment environment validation failed:\n{rendered}")
    return values


def _runtime_environment(
    values: Mapping[str, str],
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key in _CONTROLLED_EXACT or key.startswith(_CONTROLLED_PREFIXES):
            env.pop(key, None)
    env.update(values)
    return env


def _compose_command(env_file: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(COMPOSE_FILE),
        *args,
    ]


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
) -> None:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=dict(env),
        check=False,
    )
    if completed.returncode != 0:
        raise DeployError(
            f"deployment command failed with exit code {completed.returncode}"
        )


def _render_and_validate_compose(
    env_file: Path,
    *,
    env: Mapping[str, str],
) -> None:
    completed = subprocess.run(
        _compose_command(env_file, "config", "--format", "json"),
        cwd=ROOT,
        env=dict(env),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeployError("docker compose config failed")
    try:
        config = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise DeployError("docker compose config returned invalid JSON") from None
    try:
        compose_contract.validate(config)
    except SystemExit as exc:
        raise DeployError(str(exc)) from None


def _wait_for_ready(port: int, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/ready"
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    response.read(65536)
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(2)
    raise DeployError(f"local readiness check failed: {last_error}")


def deploy(
    env_file: Path,
    *,
    wait_seconds: int = 180,
    dry_run: bool = False,
) -> None:
    if not 1 <= wait_seconds <= 1800:
        raise DeployError("wait seconds must be between 1 and 1800")

    secret_file = _validate_secret_file(env_file)
    values = _load_validated_values(secret_file)
    runtime_env = _runtime_environment(values)
    _render_and_validate_compose(secret_file, env=runtime_env)

    if dry_run:
        print("production deploy preflight: ok (dry-run; no containers changed)")
        return

    _run(
        _compose_command(secret_file, "pull", "--policy", "always"),
        env=runtime_env,
    )
    _run(
        _compose_command(
            secret_file,
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            str(wait_seconds),
            "--remove-orphans",
        ),
        env=runtime_env,
    )

    try:
        port = int(values.get("RPY_API_PORT", "8000"))
    except ValueError:
        raise DeployError("RPY_API_PORT must be an integer") from None
    _wait_for_ready(port, timeout_seconds=wait_seconds)

    print(f"production deploy: ready (image={values['RPY_IMAGE']}, port={port})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and apply the canonical single-host production Compose deployment."
        )
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="Secret-managed production/staging KEY=VALUE file outside the repository",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=180,
        help="Maximum wait for Compose health and local /ready (default: 180)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate env and rendered Compose only; do not pull or change containers",
    )
    args = parser.parse_args()

    try:
        deploy(
            args.env_file,
            wait_seconds=args.wait_seconds,
            dry_run=args.dry_run,
        )
    except DeployError as exc:
        print(f"production deploy: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
