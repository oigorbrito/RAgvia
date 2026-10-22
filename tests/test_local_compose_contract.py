from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _service_block(compose: str, service: str) -> str:
    lines = compose.splitlines()
    start = lines.index(f"  {service}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_local_ports_are_loopback_only_and_postgres_password_is_required() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    _require('"127.0.0.1:${PGPORT:-5432}:5432"' in compose, "PostgreSQL host port must bind loopback")
    _require('"127.0.0.1:8000:8000"' in compose, "API host port must bind loopback")
    _require("POSTGRES_PASSWORD: rpy" not in compose, "PostgreSQL must not silently use the default password")
    _require("POSTGRES_PASSWORD:?" in compose, "new local databases must require an explicit PostgreSQL password")


def test_offline_smokes_supply_disposable_database_credentials() -> None:
    shell = (ROOT / "scripts" / "smoke_offline.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "smoke_offline.ps1").read_text(encoding="utf-8-sig")
    _require("secrets.token_hex(32)" in shell, "shell smoke must generate a throwaway password")
    _require("RandomNumberGenerator]::GetBytes(32)" in powershell, "PowerShell smoke must generate a throwaway password")
    _require("${POSTGRES_PASSWORD}" in shell, "shell smoke DATABASE_URL must match PostgreSQL password")
    _require("$($env:POSTGRES_PASSWORD)" in powershell, "PowerShell smoke DATABASE_URL must match PostgreSQL password")


def test_local_judit_credentials_and_callback_are_worker_only() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for service in ("worker-1", "worker-2"):
        block = _service_block(compose, service)
        _require("JUDIT_API_KEY: ${JUDIT_API_KEY:-}" in block, f"{service} must receive the Judit key")
        _require(
            "JUDIT_CALLBACK_URL: ${JUDIT_CALLBACK_URL:-}" in block,
            f"{service} must receive the optional callback URL",
        )

    for service in ("api", "scheduler"):
        block = _service_block(compose, service)
        _require("JUDIT_API_KEY" not in block, f"{service} must not receive the Judit key")
        _require(
            "JUDIT_CALLBACK_URL" not in block,
            f"{service} must not receive the callback URL",
        )


def test_datajud_lookup_configuration_keeps_provider_key_worker_only() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    api = _service_block(compose, "api")
    _require("DATAJUD_ENABLED" in api and "DATAJUD_AUTHORIZED_USE" in api, "API needs only non-secret activation flags")
    _require("DATAJUD_API_KEY" not in api, "API must not receive the DataJud credential")
    for service in ("worker-1", "worker-2"):
        block = _service_block(compose, service)
        _require("DATAJUD_API_KEY: ${DATAJUD_API_KEY:-}" in block, f"{service} must receive the DataJud credential")


def test_local_env_example_keeps_callback_opt_in_and_secret_free() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    _require("JUDIT_CALLBACK_URL=" in env_example, "callback opt-in must be documented")
    _require("JUDIT_API_KEY=" in env_example, "provider key field must be documented")
    _require(
        "JUDIT_CALLBACK_URL=https://" not in env_example,
        "example callback URL must not contain a configured endpoint or token",
    )
