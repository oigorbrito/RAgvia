from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import subprocess
import sys
import threading
import unicodedata
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.judit import extract_lawsuit_processes, normalize_cnj

FRONTEND_DIR = ROOT / "app" / "frontend"
REPORT_NAME = "iasummary-local-validation.json"
MARKDOWN_NAME = "iasummary-local-extractive.md"
_DOCUMENT_START = "## Documento gerado\n\n"
_DOCUMENT_END = "\n\n## Validação factual e proveniência"
_BOOTSTRAP_JS = r"""(() => {
  const showFailure = () => {
    const title = document.querySelector('#page-title');
    const lede = document.querySelector('#workspace-lede');
    const form = document.querySelector('#search-form');
    if (form) form.hidden = true;
    if (title) title.textContent = 'Pré-visualização local indisponível';
    if (lede) lede.textContent = 'A sessão local expirou ou não foi autorizada. Reinicie o comando de homologação; nenhuma consulta processual foi feita.';
  };
  const token = decodeURIComponent(location.hash.slice(1));
  history.replaceState(null, '', `${location.pathname}${location.search}`);
  if (!token) {
    showFailure();
    return;
  }
  fetch('/__local_preview_payload', {
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    cache: 'no-store',
    credentials: 'same-origin',
    referrerPolicy: 'no-referrer'
  }).then((response) => {
    if (!response.ok) throw new Error('local preview denied');
    return response.json();
  }).then((payload) => {
    window.RAGVIA_LOCAL_PREVIEW = payload;
    const app = document.createElement('script');
    app.src = '/app.js';
    app.onerror = showFailure;
    document.body.appendChild(app);
  }).catch(showFailure);
})();
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read local preview input: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"local preview input must be a JSON object: {path.name}")
    return value


def _extract_generated_markdown(document: str) -> str:
    start = document.find(_DOCUMENT_START)
    if start < 0:
        raise ValueError("generated Markdown section is missing from the existing artifact")
    body_start = start + len(_DOCUMENT_START)
    end = document.find(_DOCUMENT_END, body_start)
    if end < 0:
        raise ValueError("validation/report boundary is missing from the existing artifact")
    markdown = document[body_start:end]
    if not markdown.strip() or not markdown.lstrip().startswith("# Resumo do processo"):
        raise ValueError("existing artifact does not contain the expected generated summary")
    return markdown


def build_preview_payload(capture_path: Path, output_dir: Path) -> dict[str, Any]:
    capture_path = capture_path.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve(strict=True)
    capture_bytes = capture_path.read_bytes()
    capture_sha256 = _sha256(capture_bytes)
    capture = _read_json(capture_path)
    report = _read_json(output_dir / REPORT_NAME)
    markdown_document = (output_dir / MARKDOWN_NAME).read_text(encoding="utf-8")
    markdown = _extract_generated_markdown(markdown_document)
    markdown_sha256 = _sha256(markdown.encode("utf-8"))

    if report.get("source", {}).get("sha256") != capture_sha256:
        raise ValueError("capture hash does not match the existing validation report")
    lawsuits = extract_lawsuit_processes(capture)
    page_data = capture.get("page_data")
    lawsuit_records = [
        item for item in page_data
        if isinstance(item, dict) and item.get("response_type") == "lawsuit"
    ] if isinstance(page_data, list) else []
    if len(lawsuits) != 1 or len(lawsuit_records) != 1:
        raise ValueError("preview requires exactly one lawsuit response")
    lawsuit_data = lawsuits[0]
    lawsuit_record = lawsuit_records[0]
    steps = lawsuit_data.get("steps")
    if not isinstance(steps, list):
        raise ValueError("lawsuit response is missing its movements")

    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    generation = report.get("generation") if isinstance(report.get("generation"), dict) else {}
    retrieval = report.get("retrieval") if isinstance(report.get("retrieval"), dict) else {}
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
    movement_sources = provenance.get("movement_sources")
    claim_evidence_refs = provenance.get("claim_evidence_refs")
    errors = validation.get("errors")
    actual_movements = len(steps)
    claim_count = validation.get("claim_count")
    timeline_claim_count = validation.get("timeline_claim_count")
    evidence_complete = (
        validation.get("all_movements_have_structured_evidence_refs") is True
        and isinstance(movement_sources, list)
        and len(movement_sources) == actual_movements
        and isinstance(claim_evidence_refs, list)
        and len(claim_evidence_refs) == claim_count
        and all(
            isinstance(item, dict)
            and isinstance(item.get("source_ref"), str)
            and item["source_ref"].startswith("steps/")
            and isinstance(item.get("evidence_ref"), str)
            and bool(item["evidence_ref"])
            and re.fullmatch(r"[a-f0-9]{64}", str(item.get("source_text_sha256") or "")) is not None
            for item in movement_sources
        )
        and all(
            isinstance(item, dict)
            and isinstance(item.get("claim_id"), str)
            and isinstance(item.get("evidence_refs"), list)
            and bool(item["evidence_refs"])
            and all(isinstance(ref, str) and ref for ref in item["evidence_refs"])
            for item in claim_evidence_refs
        )
    )
    if (
        source.get("summary_reference_excluded_from_generation") is not True
        or actual_movements != 13
        or source.get("movement_count") != actual_movements
        or retrieval.get("selected_movement_count") != actual_movements
        or generation.get("model") != "rpy-local-extractive"
        or generation.get("mode") != "local_extractive_no_provider"
        or generation.get("anthropic_called") is not False
        or generation.get("persisted") is not False
        or validation.get("passed") is not True
        or not isinstance(errors, list)
        or errors
        or claim_count != 21
        or timeline_claim_count != actual_movements
        or not evidence_complete
        or int(lawsuit_data.get("secrecy_level") or 0) != 0
    ):
        raise ValueError("existing local result failed the preview's provenance/validation gate")

    code_value = lawsuit_data.get("code") or lawsuit_data.get("process_code")
    try:
        code = normalize_cnj(str(code_value or ""))
    except ValueError as exc:
        raise ValueError("capture has no valid CNJ") from exc
    classifications = source.get("normalized_classifications")
    primary_class = next(
        (
            item.get("name")
            for item in classifications or []
            if isinstance(item, dict)
            and item.get("suspect") is False
            and isinstance(item.get("name"), str)
        ),
        None,
    )
    if not primary_class:
        raise ValueError("validation report has no non-suspect primary classification")
    captured_at = lawsuit_record.get("created_at") or lawsuit_record.get("request_created_at")
    if not isinstance(captured_at, str):
        raise ValueError("capture timestamp is missing")
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("capture timestamp is invalid") from exc

    safe_movements = [
        {
            "source_ref": str(item.get("source_ref") or ""),
            "evidence_ref": str(item.get("evidence_ref") or ""),
            "source_text_sha256": str(item.get("source_text_sha256") or ""),
        }
        for item in movement_sources
    ]
    safe_claims = [
        {
            "claim_id": str(item.get("claim_id") or ""),
            "evidence_refs": [str(ref) for ref in item.get("evidence_refs", [])],
        }
        for item in claim_evidence_refs
    ]
    limitations = (
        "O gate determinístico verifica estrutura, consistência e vínculos de evidência; "
        "não comprova entailment semântico nem substitui revisão jurídica. A comparação "
        "com a referência apontou afirmações que não são demonstradas por esta captura. "
        "A execução local de processo curto não usou banco nem recuperação vetorial."
    )
    return {
        "mode": "extrativo_local",
        "published": False,
        "capture": {
            "file_name": capture_path.name,
            "sha256": capture_sha256,
            "captured_at": captured_at,
        },
        "process": {
            "code": code,
            "class_name": primary_class,
            "court": str(lawsuit_data.get("tribunal_acronym") or lawsuit_data.get("tribunal") or ""),
        },
        "markdown": markdown,
        "markdown_sha256": markdown_sha256,
        "generation": {
            "model": generation["model"],
            "anthropic_called": False,
            "persisted": False,
        },
        "validation": {
            "passed": True,
            "errors_count": len(errors),
            "movement_count": actual_movements,
            "selected_movement_count": retrieval["selected_movement_count"],
            "claim_count": claim_count,
            "timeline_claim_count": timeline_claim_count,
            "evidence_complete": evidence_complete,
            "limitation": limitations,
        },
        "evidence": {
            "movement_sources": safe_movements,
            "claim_evidence_refs": safe_claims,
        },
    }


def _normalize_visible_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"(?m)^\s*[-*]\s+", "", value)
    return " ".join(value.split())


def run_browser_verification(
    server: ThreadingHTTPServer,
    token: str,
    payload: dict[str, Any],
    screenshot_path: Path,
    evidence_path: Path,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the requested real-browser verification") from exc

    screenshot_path = screenshot_path.expanduser().resolve()
    evidence_path = evidence_path.expanduser().resolve()
    for path in (screenshot_path, evidence_path):
        if path.is_relative_to(ROOT):
            raise ValueError("private case screenshot/evidence must remain outside the repository")
        path.parent.mkdir(parents=True, exist_ok=True)

    origin = f"http://127.0.0.1:{server.server_port}"
    session_url = f"{origin}/#{token}"
    browser_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.on("request", lambda request: browser_requests.append(request.url))
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.goto(session_url, wait_until="networkidle")
        page.locator("#result-code").wait_for(state="visible", timeout=15000)

        displayed_code = page.locator("#result-code").inner_text().strip()
        if displayed_code != payload["process"]["code"]:
            raise AssertionError("browser CNJ does not match the capture")
        if page.locator("#summary-eyebrow").inner_text().strip() != "RESULTADO EXTRATIVO LOCAL · NÃO PUBLICADO":
            raise AssertionError("browser did not label this as a local unpublished extraction")
        evidence_text = page.locator("#local-preview-evidence").inner_text()
        for required in (
            payload["capture"]["file_name"],
            payload["capture"]["sha256"],
            payload["markdown_sha256"],
            "extrativo_local",
            "Claude/provedor externo",
            "não realizada",
            "não comprova entailment semântico",
        ):
            if required not in evidence_text:
                raise AssertionError(f"browser provenance is missing {required!r}")
        if (
            page.locator("#search-form").is_visible()
            or page.locator("#refresh-from-result").is_visible()
            or page.locator("#access-trigger").is_visible()
            or page.locator("#client-reference").is_visible()
            or page.locator("#document-nav").is_visible()
        ):
            raise AssertionError("tenant/acquisition controls must be hidden in local preview mode")

        expected_copy = page.evaluate("(markdown) => summaryPlainText(markdown)", payload["markdown"])
        visible_text = page.locator("#summary-body").inner_text()
        if _normalize_visible_text(visible_text) != _normalize_visible_text(expected_copy):
            raise AssertionError("visible browser summary text diverges from the rendered validated Markdown")
        if page.locator("#summary-body").inner_text().count("—") < 1:
            raise AssertionError("expected process summary content is not visible")

        page.screenshot(path=str(screenshot_path), full_page=True)
        evidence_details = page.locator("#local-preview-evidence details")
        evidence_details.locator("summary").click()
        movement_reference_count = page.locator("#local-preview-evidence details ol > li").count()
        claim_reference_count = page.locator("#local-preview-evidence details ul > li").count()
        if movement_reference_count != payload["validation"]["movement_count"]:
            raise AssertionError("browser provenance map does not show every movement reference")
        if claim_reference_count != payload["validation"]["claim_count"]:
            raise AssertionError("browser provenance map does not show every claim reference")
        page.get_by_role("button", name="Copiar resumo").click()
        page.get_by_role("button", name="Resumo copiado").wait_for(state="visible", timeout=5000)
        clipboard_text = page.evaluate("navigator.clipboard.readText()")
        clipboard_normalized = clipboard_text.replace("\r\n", "\n").replace("\r", "\n")
        if clipboard_normalized != expected_copy:
            raise AssertionError("frontend copy output differs from the canonical Markdown text projection after CRLF normalization")
        if page_errors or console_errors:
            raise AssertionError(f"browser emitted errors: {page_errors or console_errors}")
        unexpected_requests = [
            url for url in browser_requests
            if not url.startswith(origin + "/")
        ]
        if unexpected_requests:
            raise AssertionError(f"browser attempted a non-loopback request: {unexpected_requests}")
        if any("/processes" in url or "/capabilities" in url for url in browser_requests):
            raise AssertionError("local summary preview must not call process/acquisition API routes")

        evidence = {
            "evidence_kind": "real_capture_local_frontend_preview",
            "base_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "working_tree_dirty": bool(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            ),
            "browser": "Playwright Chromium",
            "browser_version": browser.version,
            "viewport": {"width": 1440, "height": 1000},
            "capture_sha256": payload["capture"]["sha256"],
            "capture_file_name": payload["capture"]["file_name"],
            "captured_at": payload["capture"]["captured_at"],
            "cnj": displayed_code,
            "method": "extrativo_local",
            "claude_called": False,
            "official_published": False,
            "source_movements": payload["validation"]["movement_count"],
            "selected_movements": payload["validation"]["selected_movement_count"],
            "claims": payload["validation"]["claim_count"],
            "timeline_claims": payload["validation"]["timeline_claim_count"],
            "validation_passed": payload["validation"]["passed"],
            "all_movement_claims_linked": payload["validation"]["evidence_complete"],
            "markdown_sha256_displayed": payload["markdown_sha256"],
            "visible_text_matches_markdown_projection": True,
            "clipboard_matches_markdown_projection": True,
            "clipboard_line_endings_normalized": clipboard_normalized != clipboard_text,
            "movement_evidence_rows_visible_on_disclosure": movement_reference_count,
            "claim_evidence_rows_visible_on_disclosure": claim_reference_count,
            "non_loopback_requests": unexpected_requests,
            "process_api_requests": 0,
            "page_errors": page_errors,
            "console_errors": console_errors,
            "screenshot": str(screenshot_path),
            "screenshot_sha256": _sha256(screenshot_path.read_bytes()),
            "limitations": payload["validation"]["limitation"],
        }
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        browser.close()
    return evidence


class LocalPreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, token: str, payload: dict[str, Any], port: int) -> None:
        self.preview_token = token
        self.preview_payload = payload
        super().__init__(("127.0.0.1", port), LocalPreviewHandler)


class LocalPreviewHandler(SimpleHTTPRequestHandler):
    server: LocalPreviewServer
    server_version = "RAGVIA-Local-Preview"
    sys_version = ""

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _reject(self, status: int, message: str = "not found") -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _trusted_local_request(self) -> bool:
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.client_address[0] != "127.0.0.1" or self.headers.get("Host") != expected_host:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{expected_host}"

    def do_GET(self) -> None:
        if not self._trusted_local_request():
            self._reject(403)
            return
        path = urlsplit(self.path).path
        if path == "/__local_preview_payload":
            authorization = self.headers.get("Authorization", "")
            scheme, _, supplied = authorization.partition(" ")
            if (
                scheme.casefold() != "bearer"
                or not supplied
                or not hmac.compare_digest(supplied, self.server.preview_token)
            ):
                self._reject(401, "local preview authentication required")
                return
            body = json.dumps(
                self.server.preview_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/__local_preview_bootstrap.js":
            body = _BOOTSTRAP_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in {"/", "/index.html"}:
            try:
                html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
            except OSError:
                self._reject(500, "local frontend assets unavailable")
                return
            original_script = '<script src="app.js" defer></script>'
            replacement = '<script src="__local_preview_bootstrap.js" defer></script>'
            if html.count(original_script) != 1:
                self._reject(500, "canonical frontend script tag changed; preview refused")
                return
            body = html.replace(original_script, replacement).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.directory = str(FRONTEND_DIR)
        super().do_GET()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve an existing validated extractive summary through the canonical frontend, loopback-only.",
    )
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0, help="loopback port; 0 selects an ephemeral port")
    parser.add_argument("--open-browser", action="store_true", help="open the local authenticated session and keep serving")
    parser.add_argument("--browser-smoke", action="store_true", help="verify Chromium rendering/copy and exit")
    parser.add_argument("--screenshot-out", type=Path, help="private full-page screenshot path")
    parser.add_argument("--evidence-out", type=Path, help="private browser evidence JSON path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not 0 <= args.port <= 65535:
        raise SystemExit("port must be between 0 and 65535")
    try:
        payload = build_preview_payload(args.capture, args.output_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"local summary preview refused: {exc}") from exc

    token = secrets.token_urlsafe(32)
    server = LocalPreviewServer(token, payload, args.port)
    origin = f"http://127.0.0.1:{server.server_port}"
    session_url = f"{origin}/#{token}"
    if args.browser_smoke:
        screenshot = args.screenshot_out or args.output_dir / "iasummary-local-preview.png"
        evidence_path = args.evidence_out or args.output_dir / "iasummary-local-preview-browser-evidence.json"
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            evidence = run_browser_verification(server, token, payload, screenshot, evidence_path)
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        return

    print(f"Preview local autenticado: {origin}/ (token temporário somente na sessão do navegador).")
    print("Para abrir, use --open-browser; nenhuma rota de processo ou aquisição é chamada.")
    if args.open_browser:
        import webbrowser

        if not webbrowser.open(session_url):
            server.server_close()
            raise SystemExit("não foi possível abrir o navegador configurado")
        print("Navegador aberto; o fragmento de autenticação será removido ao carregar a página. Ctrl+C encerra a sessão.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
