from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy_production.py"
SPEC = importlib.util.spec_from_file_location("deploy_production", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
deploy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deploy
SPEC.loader.exec_module(deploy)


def test_runtime_environment_makes_secret_file_values_authoritative() -> None:
    result = deploy._runtime_environment(
        {
            "RPY_IMAGE": "ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64,
            "JUDIT_API_KEY": "file-key",
        },
        base={
            "PATH": "/usr/bin",
            "RPY_IMAGE": "host-image",
            "JUDIT_API_KEY": "host-key",
            "DATAJUD_API_KEY": "host-datajud-key",
            "UNRELATED": "preserved",
        },
    )

    assert result["RPY_IMAGE"].endswith("a" * 64)
    assert result["JUDIT_API_KEY"] == "file-key"
    assert "DATAJUD_API_KEY" not in result
    assert result["UNRELATED"] == "preserved"


def test_compose_command_always_uses_canonical_file(tmp_path: Path) -> None:
    env_file = tmp_path / "staging.env"

    command = deploy._compose_command(env_file, "pull", "--policy", "always")

    assert command[:2] == ["docker", "compose"]
    assert command[2:4] == ["--env-file", str(env_file)]
    assert command[4:6] == ["-f", str(deploy.COMPOSE_FILE)]
    assert command[-3:] == ["pull", "--policy", "always"]


def test_secret_file_must_live_outside_repository(tmp_path: Path) -> None:
    path = deploy.ROOT / ".deploy-production-test.env"
    path.write_text("RPY_IMAGE=value\n", encoding="utf-8")
    try:
        with pytest.raises(deploy.DeployError, match="outside the repository"):
            deploy._validate_secret_file(path)
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_secret_file_rejects_group_or_world_access(tmp_path: Path) -> None:
    path = tmp_path / "staging.env"
    path.write_text("RPY_IMAGE=value\n", encoding="utf-8")
    path.chmod(0o640)

    with pytest.raises(deploy.DeployError, match="group or other"):
        deploy._validate_secret_file(path)


def test_dry_run_performs_validation_without_container_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "staging.env"
    env_file.write_text("placeholder=shape\n", encoding="utf-8")
    if os.name == "posix":
        env_file.chmod(0o600)

    monkeypatch.setattr(
        deploy,
        "_load_validated_values",
        lambda _path: {
            "RPY_IMAGE": "ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64,
        },
    )
    monkeypatch.setattr(deploy, "_render_and_validate_compose", lambda *_args, **_kwargs: None)

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("dry-run must not mutate containers")

    monkeypatch.setattr(deploy, "_run", unexpected_run)

    deploy.deploy(env_file, dry_run=True)

    assert "dry-run" in capsys.readouterr().out


def test_deploy_pulls_then_starts_and_checks_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "staging.env"
    env_file.write_text("placeholder=shape\n", encoding="utf-8")
    if os.name == "posix":
        env_file.chmod(0o600)

    values = {
        "RPY_IMAGE": "ghcr.io/oigorbrito/rpy@sha256:" + "b" * 64,
        "RPY_API_PORT": "8123",
    }
    monkeypatch.setattr(deploy, "_load_validated_values", lambda _path: values)
    monkeypatch.setattr(deploy, "_render_and_validate_compose", lambda *_args, **_kwargs: None)

    commands: list[list[str]] = []
    monkeypatch.setattr(
        deploy,
        "_run",
        lambda command, **_kwargs: commands.append(list(command)),
    )
    ready_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        deploy,
        "_wait_for_ready",
        lambda port, *, timeout_seconds: ready_calls.append((port, timeout_seconds)),
    )

    deploy.deploy(env_file, wait_seconds=240)

    assert commands[0][-3:] == ["pull", "--policy", "always"]
    assert commands[1][-6:] == [
        "up",
        "--detach",
        "--wait",
        "--wait-timeout",
        "240",
        "--remove-orphans",
    ]
    assert ready_calls == [(8123, 240)]


@pytest.mark.parametrize("value", [0, 1801])
def test_deploy_rejects_out_of_range_wait(value: int, tmp_path: Path) -> None:
    with pytest.raises(deploy.DeployError, match="between 1 and 1800"):
        deploy.deploy(tmp_path / "missing.env", wait_seconds=value)


def test_deploy_tools_import_without_site_packages() -> None:
    for script in (
        "scripts/validate_deploy_env.py",
        "scripts/validate_production_compose.py",
        "scripts/deploy_production.py",
    ):
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "import runpy; " + f"runpy.run_path({script!r}, run_name='rpy_import_probe')",
            ],
            cwd=deploy.ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, (
            script,
            completed.stdout,
            completed.stderr,
        )
