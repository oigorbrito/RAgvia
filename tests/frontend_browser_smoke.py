from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend"
VALID_CODE = "0000000-00.2026.8.21.0001"
TOKEN = "browser-smoke-token"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/capabilities":
            payload = json.dumps(
                {
                    "authenticated": False,
                    "default_source": "judit",
                    "sources": {
                        "judit": {"name": "Judit", "configured": True, "enabled": True, "experimental": False},
                        "datajud": {"name": "DataJud", "configured": False, "enabled": False, "experimental": True},
                    },
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def ready_payload(*, markdown: str = "Resumo pronto no navegador real.") -> dict:
    return {
        "code": VALID_CODE,
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "updated_at": "2026-09-20T12:00:00Z",
        "summary_status": "available",
        "summary": {
            "markdown": markdown,
            "created_at": "2026-09-20T12:00:00Z",
        },
        "parties": [{"name": "Parte Sintética", "side": "active", "person_type": "person"}],
        "subjects": [{"code": "1", "name": "Obrigação"}],
        "header": {"instance": 1, "county": "Porto Alegre", "amount": 1234.56},
        "recent_steps": [
            {
                "title": "Sentença",
                "text": "Movimento público sintético.",
                "occurred_at": "2026-09-20T10:00:00Z",
            }
        ],
    }


def fulfill_json(route: Route, status: int, payload: dict) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


def configure_ready_api(page: Page, payload: dict) -> list[dict]:
    requests: list[dict] = []

    def handler(route: Route) -> None:
        request = route.request
        requests.append(
            {
                "method": request.method,
                "url": request.url,
                "authorization": request.headers.get("authorization"),
            }
        )
        if request.method == "GET" and "/processes/" in request.url:
            fulfill_json(route, 200, payload)
            return
        fulfill_json(route, 500, {"detail": "unexpected request"})

    page.route("**/processes/**", handler)
    return requests


def fill_lookup(page: Page) -> None:
    if page.locator("#auth-panel").is_hidden():
        page.locator("#access-trigger").click()
    page.locator("#bearer-token").fill(TOKEN)
    page.locator("#process-code").fill(VALID_CODE)
    page.locator("#search-submit").click()


def run() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            console_errors: list[str] = []
            page_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            page.goto(origin, wait_until="networkidle")
            _require(page.title() == "RagV", "document title must use RagV identity")
            _require(page.locator(".brand").inner_text() == "RagV", "brand must read RagV")
            _require(page.locator(".brand-mark").is_visible(), "RagV brand icon must be visible")
            _require(page.locator(".client-reference").inner_text().strip().lower() == "grupo villela", "client reference must remain secondary to RagV")
            palette = page.evaluate("""() => ({ accent: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(), cyan: getComputedStyle(document.documentElement).getPropertyValue('--brand-cyan').trim() })""")
            _require(palette == {"accent": "#0a323c", "cyan": "#29bae3"}, "Villela-derived palette must be applied")
            _require(page.locator("#search-submit").is_visible(), "process search must be visible")
            _require(page.locator("#history-trigger").is_visible(), "history action must be visible")
            _require(page.locator("#auth-panel").is_hidden(), "access panel must start closed")
            page.set_viewport_size({"width": 360, "height": 800})
            _require(page.locator(".client-reference").is_hidden(), "client reference must yield to compact mobile navigation")
            mobile_width = page.evaluate("""() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth })""")
            _require(mobile_width["document"] <= mobile_width["viewport"], f"mobile layout overflow: {mobile_width}")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.locator("#access-trigger").click()
            _require(page.locator("#auth-panel").is_visible(), "access panel must open on request")
            _require(
                page.locator("#bearer-token").get_attribute("type") == "password",
                "Bearer token field must be masked",
            )
            _require(page.locator("#source-judit").is_checked(), "Judit must be the default source")
            _require(page.locator("#source-datajud").is_disabled(), "unconfigured DataJud must be disabled")
            _require(
                page.locator("#api-connectivity-label").inner_text() == "API RagV conectada",
                "local API connectivity status must be rendered",
            )
            page.locator("#judit-info").click()
            _require(page.locator("#judit-tooltip").is_visible(), "Judit tooltip must open")
            _require(
                "Pode gerar cobrança" in page.locator("#judit-tooltip").inner_text(),
                "Judit tooltip must disclose possible charges",
            )
            page.keyboard.press("Escape")
            _require(page.locator("#judit-tooltip").is_hidden(), "Escape must close the provider tooltip")
            page.locator("#datajud-info").hover()
            _require(page.locator("#datajud-tooltip").is_visible(), "DataJud tooltip must open")
            _require(
                "API pública do CNJ" in page.locator("#datajud-tooltip").inner_text(),
                "DataJud tooltip must describe the public API",
            )
            page.locator("#access-trigger").click()
            _require(page.locator("#auth-panel").is_hidden(), "access panel must be closable")

            requests = configure_ready_api(page, ready_payload())
            fill_lookup(page)
            page.locator("#result").wait_for(state="visible")
            _require(
                "Resumo pronto no navegador real." in page.locator("#summary-body").inner_text(),
                "synthetic summary must render",
            )
            _require(
                "Movimento público sintético." in page.locator("#movements-list").inner_text(),
                "synthetic movement must render",
            )
            _require(page.locator("#result-code").inner_text() == VALID_CODE, "CNJ must render")
            _require(bool(requests) and requests[0]["method"] == "GET", "lookup must be a read")
            _require(requests[0]["authorization"] == f"Bearer {TOKEN}", "Bearer header must be sent")
            _require(TOKEN not in requests[0]["url"], "Bearer token must not appear in the URL")

            page.unroute("**/processes/**")
            malicious = (
                "# Resumo do processo\n"
                "<ProcessHeader className=\"process-header\">\n"
                f"- Processo: {VALID_CODE}\n"
                "</ProcessHeader>\n"
                "## Partes\n"
                "<Party name=\"Parte Sintética\" />\n"
                "<img src=x onerror=alert(1)>"
            )
            configure_ready_api(page, ready_payload(markdown=malicious))
            fill_lookup(page)
            page.locator("#result").wait_for(state="visible")
            rendered = page.locator("#summary-body").inner_text()
            _require("<img src=x onerror=alert(1)>" in rendered, "unsupported markup must remain inert text")
            _require(page.locator("#summary-body img").count() == 0, "summary must not create an image node")
            _require(
                page.locator('[data-component="ProcessHeader"]').count() == 1,
                "allowlisted process header must render",
            )
            _require(
                page.locator('[data-component="Party"]').inner_text() == "Parte Sintética",
                "allowlisted synthetic party must render",
            )
            _require(console_errors == [], "browser console must have no errors")
            console_errors.clear()

            page.unroute("**/processes/**")

            def missing_handler(route: Route) -> None:
                request = route.request
                if request.method == "GET":
                    fulfill_json(route, 404, {"detail": "not found"})
                    return
                if request.method == "POST":
                    fulfill_json(route, 202, {"status": "pending", "created": True})
                    return
                fulfill_json(route, 500, {"detail": "unexpected request"})

            page.route("**/processes/**", missing_handler)
            fill_lookup(page)
            page.locator("#request-process").wait_for(state="visible")
            page.locator("#request-process").click()
            page.wait_for_function(
                'document.querySelector("#status-title").textContent.includes("Consulta registrada")'
            )

            _require(
                all(
                    "Failed to load resource: the server responded with a status of 404" in error
                    for error in console_errors
                ),
                "only the expected synthetic 404 may appear in the console",
            )
            _require(page_errors == [], "browser page must have no uncaught errors")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    run()
    print("frontend browser smoke: PASS")
