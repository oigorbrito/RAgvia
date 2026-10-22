"""RagV Desktop: janela nativa para o frontend FastAPI existente."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


URL = os.environ.get("RPY_DESKTOP_URL", "http://127.0.0.1:8000")


def get_url():
    return os.environ.get("RPY_DESKTOP_URL", URL)


def api_ready():
    try:
        with urllib.request.urlopen(
            f"{get_url()}/ready", timeout=3
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False



def show_error(message):
    """Mostra um erro mesmo sem console no Windows."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, message, "RagV", 0x10
        )
    except (AttributeError, OSError):
        print(message, file=sys.stderr)


class DesktopApi:
    def open_datajud_configurator(self):
        script = os.environ.get("RPY_DESKTOP_CONFIGURATOR", "").strip()
        if not script or not os.path.isfile(script):
            return {"ok": False, "message": "Configurador DataJud não encontrado."}
        powershell = os.environ.get(
            "RPY_POWERSHELL", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        try:
            subprocess.Popen(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, ValueError):
            return {"ok": False, "message": "Não foi possível abrir o configurador DataJud."}
        return {"ok": True}


def _inject_managed_token(window):
    token = os.environ.get("RPY_DESKTOP_TOKEN", "").strip()
    if not token:
        return
    script = (
        "(() => { const apply = () => { const input = document.querySelector('#bearer-token'); "
        "const button = document.querySelector('#verify-token'); "
        f"if (input && button) {{ input.value = {json.dumps(token)}; "
        "input.dispatchEvent(new Event('input', {bubbles:true})); button.click(); } "
        "else { setTimeout(apply, 200); } }; setTimeout(apply, 300); })()"
    )
    try:
        window.evaluate_js(script)
    except Exception:
        # The API remains protected; a failed injection leaves the normal access UI.
        pass


def main():
    if not api_ready():
        show_error("RagV indisponivel. Verifique o Docker e a API.")
        return 1

    try:
        import webview
    except ImportError:
        show_error("O componente pywebview nao foi encontrado.")
        return 2

    window = webview.create_window(
        "RagV",
        get_url(),
        width=1200,
        js_api=DesktopApi(),
        height=800,
        min_size=(800, 600),
    )
    def on_loaded(*_args):
        _inject_managed_token(window)

    window.events.loaded += on_loaded
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
