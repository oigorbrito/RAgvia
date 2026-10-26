from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_PASSWORD = "compose-config-test-only-not-a-secret"
RAGVIA_SERVICES = ("migrate", "api", "worker-1", "worker-2", "scheduler", "demo-seed")


def _compose_config(
    tmp_path: Path, *, overlay: bool, inherited_password: str | None = None
) -> dict:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is required to resolve the Compose contract")

    env_file = tmp_path / "compose-test.env"
    env_file.write_text(
        f"POSTGRES_PASSWORD={SYNTHETIC_PASSWORD}\n", encoding="ascii"
    )
    command = [
        docker,
        "compose",
        "--project-directory",
        str(ROOT),
        "--env-file",
        str(env_file),
        "--project-name",
        "ragvia" if overlay else "rpy-shared-contract",
        "--profile",
        "demo",
        "--file",
        str(ROOT / "compose.yaml"),
    ]
    if overlay:
        command.extend(("--file", str(ROOT / "compose.ragvia.yaml")))
    command.extend(("config", "--format", "json"))

    environment = os.environ.copy()
    if inherited_password is None:
        # Match the documented invocation: private env files, not an inherited
        # RPY shell variable, are authoritative for Compose interpolation.
        environment.pop("POSTGRES_PASSWORD", None)
    else:
        environment["POSTGRES_PASSWORD"] = inherited_password
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    assert result.returncode == 0, "docker compose config failed"
    return json.loads(result.stdout)


def _database_url(service: dict) -> SplitResult:
    return urlsplit(service["environment"]["DATABASE_URL"])


def test_effective_ragvia_compose_uses_only_new_identity_and_volume(
    tmp_path: Path,
) -> None:
    config = _compose_config(tmp_path, overlay=True)
    postgres = config["services"]["postgres"]

    assert config["name"] == "ragvia"
    assert postgres["environment"]["POSTGRES_DB"] == "ragvia"
    assert postgres["environment"]["POSTGRES_USER"] == "ragvia"
    assert postgres["environment"]["POSTGRES_PASSWORD"] == SYNTHETIC_PASSWORD
    assert "pg_isready -U ragvia -d ragvia" in postgres["healthcheck"]["test"][-1]
    mounts = postgres["volumes"]
    assert len(mounts) == 1
    assert (mounts[0]["type"], mounts[0]["source"], mounts[0]["target"]) == (
        "volume",
        "ragvia_pgdata",
        "/var/lib/postgresql/data",
    )
    assert config["volumes"]["ragvia_pgdata"] == {
        "name": "ragvia_pgdata",
        "external": True,
    }
    assert all(mount["source"] != "rpy_pgdata" for mount in mounts)

    for name in RAGVIA_SERVICES:
        database = _database_url(config["services"][name])
        assert (database.scheme, database.username, database.password) == (
            "postgresql",
            "ragvia",
            SYNTHETIC_PASSWORD,
        ), name
        assert (database.hostname, database.port, database.path) == (
            "postgres",
            5432,
            "/ragvia",
        ), name


def test_inherited_shell_password_takes_precedence_over_env_files(
    tmp_path: Path,
) -> None:
    inherited = "synthetic-inherited-shell-value"
    config = _compose_config(
        tmp_path, overlay=True, inherited_password=inherited
    )

    assert config["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] == inherited
    assert _database_url(config["services"]["api"]).password == inherited


def test_shared_base_compose_retains_rpy_contract_without_overlay(
    tmp_path: Path,
) -> None:
    config = _compose_config(tmp_path, overlay=False)
    postgres = config["services"]["postgres"]

    assert postgres["environment"]["POSTGRES_DB"] == "rpy"
    assert postgres["environment"]["POSTGRES_USER"] == "rpy"
    assert [volume["source"] for volume in postgres["volumes"]] == ["rpy_pgdata"]
    assert "rpy_pgdata" in config["volumes"]
    for name in RAGVIA_SERVICES:
        database = _database_url(config["services"][name])
        assert (database.username, database.password, database.path) == (
            "rpy",
            SYNTHETIC_PASSWORD,
            "/rpy",
        ), name
