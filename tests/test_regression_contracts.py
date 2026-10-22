"""Guardrails executáveis para contratos de homologação e persistência."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_config_persist_defaults_are_limited_to_first_profile_creation() -> None:
    launcher = _text("packaging/windows/start-homologacao.ps1")
    creation = launcher.index("if (-not (Test-Path $script:HmlEnv))")
    settings = launcher.index("$settings = Get-HmlSettings")
    assert "DATAJUD_ENABLED=false" in launcher[creation:settings]
    assert "DATAJUD_AUTHORIZED_USE=false" in launcher[creation:settings]
    assert "DATAJUD_ENABLED=false" not in launcher[settings:]
    assert "DATAJUD_AUTHORIZED_USE=false" not in launcher[settings:]


def test_datajud_restart_contract_recreates_only_provider_receivers() -> None:
    script = _text("packaging/windows/configurar-datajud.ps1")
    assert "DATAJUD_ENABLED=true" in script
    assert "DATAJUD_AUTHORIZED_USE=true" in script
    assert "DATAJUD_API_KEY=$key" in script
    recreate = script[script.index("Invoke-HmlDocker") :]
    assert "'api','worker-1','worker-2','scheduler'" in recreate
    assert "'postgres'" not in recreate
    assert "Wait-DataJudCapabilities" in script


def test_datajud_key_stays_out_of_api_environment_contract() -> None:
    compose = _text("compose.yaml")
    api_start = compose.index("  api:")
    worker_start = compose.index("  worker-1:")
    api = compose[api_start:worker_start]
    assert "DATAJUD_ENABLED" in api
    assert "DATAJUD_AUTHORIZED_USE" in api
    assert "DATAJUD_API_KEY" not in api
    assert "DATAJUD_API_KEY: ${DATAJUD_API_KEY:-}" in compose[worker_start:]


def test_homologation_does_not_reintroduce_password_login() -> None:
    launcher = _text("packaging/windows/start-homologacao.ps1")
    installer = _text("packaging/windows/RagVDesktop.iss")
    assert "Ensure-HmlPassword" not in launcher
    assert "redefinir-senha.ps1" not in installer
    assert "Redefinir senha local" not in installer


def test_homologation_launcher_has_no_habitual_project_reference() -> None:
    launcher = _text("packaging/windows/start-homologacao.ps1")
    common = _text("packaging/windows/homologacao-common.ps1")
    assert "D:\\projetos" not in launcher
    assert "ragv_rpy_pgdata" not in launcher
    assert "ragv_rpy_pgdata" not in common


def test_desktop_backend_and_auth_contracts_remain_explicit() -> None:
    desktop = _text("scripts/ragv_desktop.py")
    launcher = _text("packaging/windows/start-homologacao.ps1")
    assert "RPY_DESKTOP_URL" in launcher
    assert "RPY_DESKTOP_TOKEN" in launcher
    assert "RPY_DESKTOP_TOKEN" in desktop
    assert "evaluate_js" in desktop
    assert "bearer-token" in desktop
    assert "http" not in desktop.split("evaluate_js", 1)[-1].split("bearer-token", 1)[0]


def test_datajud_selector_follows_capabilities_without_secret_exposure() -> None:
    frontend = _text("app/frontend/app.js")
    html = _text("app/frontend/index.html")
    assert "data.sources && data.sources.datajud" in frontend
    assert "sourceDataJudRadio.disabled = false" in frontend
    assert "sourceDataJudRadio.disabled = true" in frontend
    assert "DATAJUD_API_KEY" not in html
    assert "DATAJUD_API_KEY" not in frontend


def test_datajud_e2e_contract_is_explicitly_external_and_not_automatic() -> None:
    frontend = _text("app/frontend/app.js")
    assert "metadata_lookup" not in frontend or "DataJud" in frontend
    assert "source-datajud" in frontend
    assert "Judit" in frontend
