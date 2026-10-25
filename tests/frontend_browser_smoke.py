from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, Route, expect, sync_playwright


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


def fill_lookup(page: Page, *, token: str = TOKEN, code: str = VALID_CODE) -> None:
    if page.locator("#auth-panel").is_hidden():
        page.locator("#access-trigger").click()
    page.locator("#bearer-token").fill(token)
    page.locator("#process-code").fill(code)
    page.locator("#search-submit").click()


def run(*, api_response: dict | None = None) -> None:
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
                "<script>window.__summary_xss = true</script>"
                "<strong onmouseover=alert(1)>malicious</strong>"
            )
            configure_ready_api(page, ready_payload(markdown=malicious))
            fill_lookup(page)
            page.locator("#result").wait_for(state="visible")
            rendered = page.locator("#summary-body").inner_text()
            _require("<img src=x onerror=alert(1)>" in rendered, "unsupported markup must remain inert text")
            _require("<script>window.__summary_xss = true</script>" in rendered, "script markup must remain inert text")
            _require(page.locator("#summary-body img, #summary-body script, #summary-body strong[onmouseover]").count() == 0, "summary must not create executable or event-handler nodes")
            _require(page.evaluate("window.__summary_xss === undefined"), "summary script content must not execute")
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
            if api_response is not None:
                # Replay the exact response exported by the PostgreSQL canonical E2E,
                # not an independently authored summary fixture.
                _require(isinstance(api_response.get("iaSummary"), str), "published Markdown required")
                _require(api_response["summary"]["validation"]["passed"] is True, "validated summary required")
                page.unroute("**/processes/**")
                configure_ready_api(page, api_response)
                fill_lookup(page)
                page.locator("#result").wait_for(state="visible")
                code = api_response["code"]
                page.wait_for_function(
                    "code => document.querySelector('#result-code').textContent === code", arg=code
                )
                _require(code in page.locator("#summary-body").inner_text(), "canonical summary must render")
                browser.contexts[0].grant_permissions(["clipboard-read", "clipboard-write"])
                page.locator("#copy-summary").click()
                page.wait_for_function("document.querySelector('#copy-summary').textContent.includes('copiado')")
                copied = page.evaluate("navigator.clipboard.readText()")
                _require(code in copied, "canonical summary must reach the clipboard")
                _require("<ProcessHeader" not in copied, "clipboard must contain readable text")
                _require(page_errors == [], "canonical summary must not cause browser errors")
                print("canonical PostgreSQL API response -> browser presentation/copy: PASS (synthetic)")
            # A format-only iaSummary response has no legacy summary object.
            # Use a fresh page to ensure a rendering failure cannot be masked
            # by a previous successful result or silently caught as networking.
            isolated = browser.new_page()
            isolated_errors: list[str] = []
            isolated.on("pageerror", lambda exc: isolated_errors.append(str(exc)))
            isolated.goto(origin, wait_until="networkidle")
            isolated_response = ready_payload(
                markdown="Não deve depender do campo summary."
            )
            isolated_response["summary"] = None
            isolated_response["iaSummary"] = (
                "# Resumo do processo\\n\\n"
                "Resumo jurídico sintético disponível somente por iaSummary."
            )
            configure_ready_api(isolated, isolated_response)
            fill_lookup(isolated)
            expect(isolated.locator("#result")).to_be_visible(timeout=5000)
            _require(
                "disponível somente por iaSummary"
                in isolated.locator("#summary-body").inner_text(),
                "iaSummary-only content must be visible without legacy summary",
            )
            _require(
                isolated.locator("#status-title").inner_text()
                != "Sem conexão com o serviço",
                "rendering must not be misdiagnosed as a network failure",
            )
            _require(isolated_errors == [], "iaSummary-only browser must not throw")
            isolated.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _format_header_entry(label: str, value: str) -> str:
    if label == "Processo":
        digits = re.sub(r"\D", "", value)[:20]
        if len(digits) == 20:
            value = f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:]}"
    elif label == "Instância" and value.isdigit():
        value = f"{value}ª instância"
    elif label == "Valor":
        normalized = value.strip().removeprefix("R$").strip().replace(".", "").replace(",", ".")
        try:
            value = f"R$ {float(normalized):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        except ValueError:
            pass
    elif label.startswith("Data de distribuição"):
        try:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                parsed = datetime.fromisoformat(value)
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo:
                    parsed = parsed.astimezone(ZoneInfo("America/Sao_Paulo"))
            value = parsed.strftime("%d/%m/%Y")
        except ValueError:
            pass
    return f"{label}: {value}"


def _expected_clipboard(markdown: str) -> str:
    lines = []
    in_header = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
        elif line.startswith('<ProcessHeader className="process-header">'):
            in_header = True
        elif line == "</ProcessHeader>":
            in_header = False
        elif match := re.match(r"^<Party\s+name=([\"'])([^\"'<>]+)\1\s*/>$", line):
            lines.append(match.group(2))
        elif match := re.match(r"^#{1,3}\s+(.+)$", line):
            lines.append(match.group(1))
        elif match := re.match(r"^[-*]\s+(.+)$", line):
            value = match.group(1)
            if in_header and (header := re.match(r"^([^:]+):\s*(.*)$", value)):
                value = _format_header_entry(header.group(1).strip(), header.group(2).strip())
            lines.append(f"- {value}")
        elif match := re.match(r"^Contexto do processo:\s*(.+?)\s+Andamento processual:\s*(.+)$", line, re.IGNORECASE):
            lines.append(f"Contexto do processo: {match.group(1).strip()}")
            lines.append(f"Andamento processual: {match.group(2).strip()}")
        else:
            lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def run_connected_api(*, api_origin: str, api_response: dict, token: str, screenshot_out: Path | None = None) -> dict:
    _require(api_response.get("summary", {}).get("validation", {}).get("passed") is True,
             "connected API artifact must be validated")
    _require(api_response.get("iaSummary") == api_response.get("summary", {}).get("markdown"),
             "API Markdown must equal the validated summary")
    code = api_response["code"]
    requests: list[dict] = []
    responses: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.on("request", lambda request: requests.append({
            "url": request.url,
            "authorization": request.headers.get("authorization"),
        }))
        page.on("response", lambda response: responses.append({
            "url": response.url, "status": response.status,
        }))
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.goto(api_origin, wait_until="networkidle")
        fill_lookup(page, token=token, code=code)
        try:
            page.locator("#result").wait_for(state="visible", timeout=10000)
        except Exception as exc:
            safe_responses = [item for item in responses if "/processes/" in item["url"]]
            raise AssertionError(
                f"connected API did not render the process; responses={safe_responses}; page_errors={page_errors}"
            ) from exc
        summary_body = page.locator("#summary-body")
        summary_body.wait_for(state="visible")
        rendered = summary_body.inner_text()
        context_paragraph = summary_body.locator(".summary-introduction-context")
        progress_paragraph = summary_body.locator(".summary-introduction-progress")
        _require(context_paragraph.count() == 1, "process context must render as its own paragraph")
        _require(progress_paragraph.count() == 1, "procedural progress must render as its own paragraph")
        _require("Contexto do processo:" in context_paragraph.inner_text(), "context paragraph label must remain visible")
        _require("Andamento processual:" in progress_paragraph.inner_text(), "progress paragraph label must remain visible")
        header_text = summary_body.locator(".summary-process-header").inner_text()
        _require("Data de distribuição registrada: 01/09/2026" in header_text, "distribution date must be localized in the process header")
        _require("1ª instância" in header_text and "R$ 30.000,00" in header_text, "header values must use Portuguese presentation formats")
        _require("Processo:" in header_text and "Classe:" in header_text and "Tribunal:" in header_text, "process header labels must remain in Portuguese")
        _require("2026-09-01T" not in header_text, "raw ISO timestamp must not be shown in the process header")
        _require(rendered.count("Pessoa Sintética Beta") == 1, "one representative serving multiple parties must not be repeated")
        _require("className=" not in rendered and "<ProcessHeader" not in rendered and "<Party" not in rendered, "internal JSX/HTML syntax must not be visible")
        _require("schema_version" not in rendered and '"process":' not in rendered and '"summary":' not in rendered, "raw structured JSON must not be visible")
        movement_list = summary_body.get_by_role("heading", name="Movimentações").locator("xpath=following-sibling::ul[1]")
        movement_dates = [item.locator("time").first.inner_text() for item in movement_list.locator("li").all()]
        _require(movement_dates[0] == "01/09/2026" and movement_dates[-1] == "02/09/2026", "summary chronology must remain in source order")
        _require(summary_body.locator(".summary-date-highlight[datetime]").count() >= 1, "movement dates must be semantically highlighted")
        for phrase in (
            "Contexto do processo:", "Andamento processual:", "Partes",
            "Representantes", "Classe", "Assuntos", "Movimentações",
            "Pontos de atenção", "Pessoa Sintética Alfa", "03/03/2027",
        ):
            _require(phrase in rendered, f"browser document is missing {phrase!r}")
        page.locator("#copy-summary").click()
        from playwright.sync_api import expect
        expect(page.locator("#copy-summary")).to_contain_text("copiado")
        copied = page.evaluate("navigator.clipboard.readText()")
        expected_copy = _expected_clipboard(api_response["iaSummary"])
        _require(copied.replace("\r\n", "\n") == expected_copy, "clipboard must preserve the exact API text apart from platform line endings")
        for fact in ("01/09/2026", "03/03/2027", "15:00", "Pessoa Sintética Alfa"):
            _require(fact in copied, f"clipboard changed or omitted {fact!r}")
        _require(all(request["authorization"] != f"Bearer {token}" or "/processes/" in request["url"] for request in requests),
                 "Bearer token must only accompany the authenticated process request")
        _require(any(request["url"].startswith(api_origin) and "/processes/" in request["url"] for request in requests),
                 "browser must fetch the process from the connected local API")
        _require(page_errors == [], f"browser errors: {page_errors}")
        if screenshot_out:
            screenshot_out.parent.mkdir(parents=True, exist_ok=True)
            expect(page.locator("#copy-summary")).to_have_text("Copiar resumo", timeout=4000)
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.screenshot(path=str(screenshot_out), full_page=True)
        evidence = {
            "evidence_kind": "synthetic_local_api_browser_smoke",
            "fixture": "tests/fixtures/judit/parser_parity_synthetic.json",
            "fixture_sha256": hashlib.sha256((ROOT / "tests/fixtures/judit/parser_parity_synthetic.json").read_bytes()).hexdigest(),
            "browser": "Playwright Chromium",
            "browser_version": browser.version,
            "viewport": {"width": 1440, "height": 900},
            "screenshot": str(screenshot_out).replace("\\", "/") if screenshot_out else None,
            "process_code": code,
            "summary_sha256": hashlib.sha256(api_response["iaSummary"].encode("utf-8")).hexdigest(),
            "rendered_text_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "clipboard_text_sha256": hashlib.sha256(copied.encode("utf-8")).hexdigest(),
            "checks": [
                "PostgreSQL API response is validated and iaSummary equals validated Markdown",
                "Context and procedural progress render as separate labeled paragraphs",
                "Process header uses localized CNJ, amount, instance, and distribution date values",
                "Party/representative relations render without repeating a representative",
                "Class, subjects, chronological movements, and attention sections are present",
                "No raw JSX/HTML or JSON is visible; adversarial markup does not execute",
                "Clipboard retains the complete readable summary projection",
                "Browser fetches the process from the local API with authorization scoped to process requests",
            ],
            "result": "PASS",
        }
        browser.close()
    print("local API -> browser presentation/copy: PASS (synthetic)")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-response", type=Path,
                        help="Synthetic response exported by the PostgreSQL canonical E2E")
    parser.add_argument("--api-origin", help="Connect the browser directly to this local FastAPI origin")
    parser.add_argument("--api-token", help="Synthetic test-only Bearer token for the connected API")
    parser.add_argument("--evidence-out", type=Path, help="Optional path for sanitized browser-smoke evidence JSON")
    parser.add_argument("--screenshot-out", type=Path, help="Optional full-page screenshot of the synthetic connected API result")
    args = parser.parse_args()
    response = json.loads(args.api_response.read_text(encoding="utf-8")) if args.api_response else None
    run(api_response=response)
    if args.api_origin:
        if not response or not args.api_token:
            parser.error("--api-origin requires --api-response and --api-token")
        evidence = run_connected_api(api_origin=args.api_origin.rstrip("/"), api_response=response, token=args.api_token, screenshot_out=args.screenshot_out)
        if args.evidence_out:
            args.evidence_out.parent.mkdir(parents=True, exist_ok=True)
            args.evidence_out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("frontend browser smoke: PASS")
