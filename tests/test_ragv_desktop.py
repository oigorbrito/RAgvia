"""Testes isolados do launcher RagV Desktop."""

import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "ragv_desktop.py"

spec = importlib.util.spec_from_file_location(
    "ragv_desktop", LAUNCHER
)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopLauncherTests(unittest.TestCase):

    def test_packaged_desktop_embeds_ragv_v_icon(self):
        icon = ROOT / "app" / "frontend" / "ragv.ico"
        spec_file = ROOT / "RagVDesktop.spec"
        self.assertTrue(icon.is_file())
        self.assertEqual(icon.read_bytes()[:4], bytes((0, 0, 1, 0)))
        self.assertIn("icon='app/frontend/ragv.ico'", spec_file.read_text(encoding="utf-8"))

    @patch.object(desktop.urllib.request, "urlopen")
    def test_api_ready_success(self, urlopen):
        response = MagicMock()
        response.status = 200
        urlopen.return_value.__enter__.return_value = response

        self.assertTrue(desktop.api_ready())

    @patch.object(desktop.urllib.request, "urlopen")
    def test_api_ready_unavailable(self, urlopen):
        urlopen.side_effect = OSError("connection refused")

        self.assertFalse(desktop.api_ready())

    @patch.object(desktop, "show_error")
    @patch.object(desktop, "api_ready", return_value=False)
    def test_main_fails_when_api_unavailable(self, _ready, mock_show_error):
        self.assertEqual(desktop.main(), 1)
        mock_show_error.assert_called_once_with(
            "RagV indisponivel. Verifique o Docker e a API."
        )

    @patch.object(desktop, "show_error")
    @patch.object(desktop, "api_ready", return_value=True)
    def test_main_fails_when_webview_missing(self, _ready, mock_show_error):
        with patch.dict(sys.modules, {"webview": None}):
            self.assertEqual(desktop.main(), 2)
            mock_show_error.assert_called_once_with(
                "O componente pywebview nao foi encontrado."
            )

    @patch.object(desktop, "api_ready", return_value=True)
    def test_main_opens_existing_frontend(self, _ready):
        fake_webview = MagicMock()

        with patch.dict(sys.modules, {"webview": fake_webview}):
            result = desktop.main()

        self.assertEqual(result, 0)
        fake_webview.create_window.assert_called_once()
        fake_webview.start.assert_called_once()

        args, kwargs = fake_webview.create_window.call_args
        self.assertEqual(args[0], "RagV")
        self.assertEqual(args[1], desktop.URL)

    @patch.dict("os.environ", {"RPY_DESKTOP_TOKEN": "managed-token"})
    @patch.object(desktop, "api_ready", return_value=True)
    def test_main_injects_managed_token_without_manual_bearer(self, _ready):
        fake_webview = MagicMock()
        window = fake_webview.create_window.return_value
        with patch.dict(sys.modules, {"webview": fake_webview}):
            self.assertEqual(desktop.main(), 0)
        fake_webview.start.assert_called_once()

    @patch("subprocess.Popen")
    def test_datajud_configurator_uses_packaged_script(self, popen):
        with patch.dict(
            "os.environ",
            {"RPY_DESKTOP_CONFIGURATOR": "C:\\RagV\\configurar-datajud.ps1"},
        ), patch.object(desktop.os.path, "isfile", return_value=True):
            result = desktop.DesktopApi().open_datajud_configurator()
        self.assertEqual(result, {"ok": True})
        popen.assert_called_once()

    def test_managed_token_script_does_not_expose_token_in_url(self):
        with patch.dict("os.environ", {"RPY_DESKTOP_TOKEN": "managed-token"}):
            window = MagicMock()
            desktop._inject_managed_token(window)
        script = window.evaluate_js.call_args.args[0]
        self.assertIn("bearer-token", script)
        self.assertIn("managed-token", script)
        self.assertNotIn("http", script.split("input.value", 1)[0])

    @patch.object(desktop, "api_ready", return_value=True)
    def test_main_custom_url_from_env(self, _ready):
        fake_webview = MagicMock()

        with patch.dict(sys.modules, {"webview": fake_webview}), patch.dict(
            "os.environ", {"RPY_DESKTOP_URL": "http://127.0.0.1:8008"}
        ):
            result = desktop.main()

        self.assertEqual(result, 0)
        args, _ = fake_webview.create_window.call_args
        self.assertEqual(args[1], "http://127.0.0.1:8008")

    def test_show_error_native_dialog(self):
        mock_msgbox = MagicMock()
        fake_windll = MagicMock()
        fake_windll.user32.MessageBoxW = mock_msgbox

        with patch("ctypes.windll", fake_windll, create=True):
            desktop.show_error("Falha de teste")

        mock_msgbox.assert_called_once_with(
            None, "Falha de teste", "RagV", 0x10
        )

    @patch("sys.stderr")
    def test_show_error_fallback(self, mock_stderr):
        fake_windll = MagicMock()
        fake_windll.user32.MessageBoxW.side_effect = OSError("sem suporte")

        with patch("ctypes.windll", fake_windll, create=True):
            desktop.show_error("Erro de fallback")

        mock_stderr.write.assert_called()


if __name__ == "__main__":
    unittest.main()
