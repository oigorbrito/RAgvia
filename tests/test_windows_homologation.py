"""Guardrails baratos; o aceite exige instalação e WebView2 reais no Windows."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.bootstrap_homologation import tenant_from_environment

ROOT = Path(__file__).resolve().parents[1]


class HomologationPackageTests(unittest.TestCase):
    def test_installer_uses_the_chosen_app_directory_for_runtime(self):
        text = (ROOT / "packaging/windows/RagVDesktop.iss").read_text(encoding="utf-8-sig")
        self.assertNotIn("B65C2D92-5C34-4B84-93C1-3DB9D8C27A71", text)
        self.assertNotIn("SetIniString", text)
        self.assertNotIn("ProjectPage", text)
        self.assertIn('"{app}\\runtime"', text)
        self.assertIn('_homologation-payload\\project\\*', text)
        self.assertIn("CurStep = ssPostInstall", text)
        self.assertIn("PrivilegesRequired=lowest", text)
        self.assertNotIn("redefinir-senha.ps1", text)
        self.assertNotIn("Redefinir senha local", text)
        self.assertIn("configurar-datajud.ps1", text)

    def test_windows_scripts_are_utf8_bom(self):
        for path in (ROOT / "packaging/windows").glob("*.ps1"):
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'), path.name)
            path.read_bytes().decode("utf-8-sig")

    def test_private_paths_fail_closed_without_powershell_security_module(self):
        text = (ROOT / "packaging/windows/homologacao-common.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("Get-Acl", text)
        self.assertNotIn("Import-Module", text)
        self.assertIn("SetAccessRuleProtection($true, $false)", text)
        self.assertIn("$actual.AreAccessRulesProtected", text)
        self.assertIn("$actual.GetAccessRules", text)
        self.assertIn("--env-file", text)
        self.assertIn("'--project-name'", text)
        self.assertIn("'runtime'", text)
        self.assertIn("Remove-Item \"Env:$name\"", text)

    def test_launcher_checks_tenant_inside_the_healthy_api(self):
        text = (ROOT / "packaging/windows/start-homologacao.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("'ragv-hml-'", text)
        self.assertIn("New-HmlSecret", text)
        self.assertNotIn("D:\\projetos", text)
        self.assertNotIn("down -v", text)
        self.assertIn("IsNullOrWhiteSpace($images)", text)
        self.assertIn("'--no-build'", text)
        self.assertIn("'exec','-T','api','python','scripts/bootstrap_homologation.py'", text)
        self.assertIn("$report.tenant_exists", text)
        self.assertIn("ragv-hml-local-test-2026", text)
        self.assertNotIn("Ensure-HmlPassword", text)
        self.assertLess(text.index("if (-not $ready)"), text.index("$tenantReport ="))

    def test_overlay_has_no_postgres_publication_and_loopback_api(self):
        text = (ROOT / "compose.homologacao.yaml").read_text()
        self.assertIn("ports: !reset []", text)
        self.assertIn("ports: !override", text)
        self.assertIn("127.0.0.1:${RPY_API_PORT:-18080}:8000", text)
        self.assertIn("RPY_HOMOLOGATION_MODE", text)
        self.assertIn("RPY_HOMOLOGATION_BIND_ADDRESS", text)
        self.assertNotIn("ragv_rpy_pgdata", text)

    def test_datajud_configurator_uses_cnj_and_recreates_only_homologation_services(self):
        text = (ROOT / "packaging/windows/configurar-datajud.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("https://datajud-wiki.cnj.jus.br/api-publica/acesso/", text)
        self.assertIn("Get-OfficialDataJudApiKey", text)
        self.assertIn("Wait-DataJudCapabilities", text)
        self.assertIn("ResponseUri", text)
        self.assertIn("DATAJUD_AUTHORIZED_USE=true", text)
        self.assertIn("worker-1','worker-2','scheduler", text)
        self.assertNotIn("postgres", text.split("Invoke-HmlDocker", 1)[-1])

    def test_package_has_allowlisted_sources_and_explicit_sha(self):
        text = (ROOT / "scripts/package_windows_homologation.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("git -C $root ls-files", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("Package SHA:", text)
        self.assertIn("requirements/windows-build.txt", text)
        self.assertNotIn("Copy-Item (Join-Path $root '.env')", text)

    def test_standard_homologation_token_requires_explicit_loopback_markers(self):
        with patch.dict(
            "os.environ",
            {"RPY_BEARER_TOKENS": json.dumps({"ragv-hml-local-test-2026": "00000000-0000-0000-0000-000000000001"})},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                tenant_from_environment()
        with patch.dict(
            "os.environ",
            {
                "RPY_BEARER_TOKENS": json.dumps({"ragv-hml-local-test-2026": "00000000-0000-0000-0000-000000000001"}),
                "RPY_HOMOLOGATION_MODE": "true",
                "RPY_HOMOLOGATION_BIND_ADDRESS": "127.0.0.1",
            },
            clear=True,
        ):
            self.assertEqual(str(tenant_from_environment()), "00000000-0000-0000-0000-000000000001")

    def test_tenant_validation_rejects_missing_environment_and_bad_uuid(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(KeyError):
                tenant_from_environment()
        with patch.dict("os.environ", {"RPY_BEARER_TOKENS": json.dumps({"x" * 32: "invalid"})}):
            with self.assertRaises(ValueError):
                tenant_from_environment()

    def test_bootstrap_is_idempotent_empty_tenant_only(self):
        text = (ROOT / "scripts/bootstrap_homologation.py").read_text()
        self.assertIn("ON CONFLICT (id) DO NOTHING", text)
        self.assertIn("tenant_exists", text)
        self.assertNotIn("INSERT INTO processes", text)
        self.assertNotIn("JUDIT_API_KEY", text)
        self.assertNotIn("print(str(", text)


if __name__ == "__main__":
    unittest.main()
