from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.judit import extract_lawsuit_processes, extract_promotable_fields, normalize_cnj

FRONTEND_DIR = ROOT / "app" / "frontend"
REPORT_NAME = "iasummary-local-validation.json"
MARKDOWN_NAME = "iasummary-local-extractive.md"
IMPORT_MANIFEST_NAME = "local-import-manifest.json"
IMPORT_CAPTURE_NAME = "source-capture.json"
MAX_IMPORT_BYTES = 10 * 1024 * 1024
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
  window.RAGVIA_LOCAL_PREVIEW_TOKEN = token;
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
    normalized = extract_promotable_fields(
        {"response_type": "lawsuit", "response_data": lawsuit_data}
    )
    source_steps = {
        str(step.get("metadata", {}).get("source_parser", {}).get("source_ref")): step
        for step in normalized["steps"]
        if isinstance(step, dict)
    }
    movement_items = []
    for source in sorted(movement_sources, key=lambda item: int(item.get("step_number", 0))):
        step = source_steps.get(str(source.get("source_ref") or ""))
        if step is None:
            raise ValueError("movement provenance does not match the normalized source")
        occurred_at = step.get("occurred_at")
        movement_text = str(step.get("text") or "")
        movement_text_sha256 = _sha256(movement_text.encode("utf-8"))
        if movement_text_sha256 != source.get("source_text_sha256"):
            raise ValueError("rendered movement text does not match its provenance hash")
        movement_items.append(
            {
                "step_number": source.get("step_number"),
                "occurred_at": occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at,
                "title": str(step.get("title") or "Movimentação"),
                "text": movement_text,
                "source_ref": str(source.get("source_ref") or ""),
                "evidence_ref": str(source.get("evidence_ref") or ""),
                "source_text_sha256": movement_text_sha256,
            }
        )
    if len(movement_items) != actual_movements:
        raise ValueError("not every captured movement has a rendered evidence link")
    safe_header_keys = (
        "name", "instance", "area", "justice_description", "county", "state", "city", "amount"
    )
    safe_header = {
        key: normalized["header"].get(key)
        for key in safe_header_keys
        if normalized["header"].get(key) is not None
    }
    limitations = (
        "O gate determinístico verifica estrutura, consistência e vínculos de evidência; "
        "não comprova entailment semântico nem substitui revisão jurídica. A geração, "
        "validação e apresentação usam somente a resposta lawsuit. A execução local "
        "de processo curto não usou banco nem recuperação vetorial."
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
            "class_name": normalized.get("class_name") or primary_class,
            "court": str(normalized.get("court") or lawsuit_data.get("tribunal_acronym") or lawsuit_data.get("tribunal") or ""),
            "parties": normalized.get("parties", []),
            "subjects": normalized.get("subjects", []),
            "header": safe_header,
        },
        "movements": movement_items,
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
        "import_enabled": False,
        "storage": {
            "persisted": False,
            "scope": "not-stored",
            "official_process_summary_persisted": False,
            "official_api_published": False,
            "tenant_association_created": False,
            "reused": False,
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
    import_file: Path | None = None,
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
        if import_file is not None:
            page.locator("#local-capture-file").set_input_files(str(import_file))
            page.locator("#local-import-submit").click()
        page.locator("#result-code").wait_for(state="visible", timeout=180000)
        if import_file is not None:
            payload = page.evaluate("() => window.RAGVIA_LOCAL_PREVIEW")
            if not isinstance(payload, dict) or payload.get("storage", {}).get("persisted") is not True:
                raise AssertionError("browser import did not return a locally persisted result")

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
            "Claude ou outro provedor externo",
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
            or not page.locator("#document-nav").is_visible()
            or not page.locator("#movements-section").is_visible()
        ):
            raise AssertionError("local view exposed tenant controls or hid the source movement view")
        if import_file is not None and not page.locator("#local-import-panel").is_visible():
            raise AssertionError("local import UI disappeared after the import")

        movement_rows = page.locator("#movements-list > li.timeline-item")
        if movement_rows.count() != len(payload["movements"]):
            raise AssertionError("browser did not render every movement from the imported lawsuit")
        for index, movement in enumerate(payload["movements"]):
            row_text = movement_rows.nth(index).inner_text()
            if movement["source_ref"] not in row_text or movement["evidence_ref"] not in row_text:
                raise AssertionError("a displayed movement is missing its local evidence reference")
            if movement["text"] and _normalize_visible_text(movement["text"]) not in _normalize_visible_text(row_text):
                raise AssertionError("displayed movement text diverges from the normalized lawsuit source")

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
            "evidence_kind": "real_capture_local_import_and_frontend_preview" if import_file is not None else "real_capture_local_frontend_preview",
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
            "locally_persisted": payload.get("storage", {}).get("persisted") is True,
            "local_record_reused": payload.get("storage", {}).get("reused") is True,
            "local_record_id_sha256": payload.get("storage", {}).get("record_id"),
            "import_posts": sum(url.endswith("/__local_import") for url in browser_requests),
            "source_movements": payload["validation"]["movement_count"],
            "movement_items_rendered": movement_rows.count(),
            "movement_text_and_evidence_match_source": True,
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


class LocalImportRejected(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def local_import_shell_payload() -> dict[str, Any]:
    return {
        "mode": "local_import",
        "published": False,
        "import_enabled": True,
        "storage": {
            "persisted": False,
            "scope": "local-development-only",
            "official_process_summary_persisted": False,
            "official_api_published": False,
            "tenant_association_created": False,
        },
    }


def _safe_source_file_name(value: str) -> str:
    try:
        name = unquote(value, errors="strict").strip()
    except (UnicodeDecodeError, ValueError) as exc:
        raise LocalImportRejected(400, "invalid_file_name") from exc
    if (
        not name
        or len(name) > 180
        or name in {".", ".."}
        or any(char in name for char in ("/", "\\", ":", "\x00", "\r", "\n"))
        or any(ord(char) < 32 for char in name)
        or not name.lower().endswith(".json")
    ):
        raise LocalImportRejected(400, "invalid_file_name")
    return name


def _local_store_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == ROOT or root.is_relative_to(ROOT):
        raise LocalImportRejected(400, "store_must_be_outside_checkout")
    root.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        root.chmod(0o700)
    return root


def _stored_payload(record_dir: Path, digest: str, *, reused: bool) -> dict[str, Any]:
    try:
        if record_dir.is_symlink() or not record_dir.is_dir():
            raise ValueError("local record is not a regular directory")
        manifest = _read_json(record_dir / IMPORT_MANIFEST_NAME)
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        generation = (
            manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
        )
        official = (
            manifest.get("official_persistence")
            if isinstance(manifest.get("official_persistence"), dict)
            else {}
        )
        file_name = source.get("file_name")
        if (
            manifest.get("schema") != 1
            or manifest.get("record_id") != digest
            or manifest.get("capture_sha256") != digest
            or source.get("origin") != "operator_supplied_existing_json_capture"
            or source.get("capture_sha256") != digest
            or source.get("summary_reference_used_for_generation") is not False
            or source.get("summary_response_consumed") is not False
            or source.get("judit_acquisition_created") is not False
            or source.get("tenant_association_created") is not False
            or not isinstance(file_name, str)
            or not file_name.lower().endswith(".json")
            or len(file_name) > 180
            or any(char in file_name for char in ("/", "\\", ":", "\x00", "\r", "\n"))
            or generation.get("method") != "rpy-local-extractive"
            or generation.get("provider") is not None
            or generation.get("provider_calls") != 0
            or official.get("process_summaries_row_created") is not False
            or official.get("api_published") is not False
        ):
            raise ValueError("stored import manifest mismatch")
        capture_path = record_dir / IMPORT_CAPTURE_NAME
        if _sha256(capture_path.read_bytes()) != digest:
            raise ValueError("stored capture hash mismatch")
        payload = build_preview_payload(capture_path, record_dir)
        if (
            generation.get("markdown_sha256") != payload["markdown_sha256"]
            or generation.get("movement_count") != payload["validation"]["movement_count"]
            or generation.get("claim_count") != payload["validation"]["claim_count"]
        ):
            raise ValueError("stored import result does not match its manifest")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LocalImportRejected(409, "existing_local_record_invalid") from exc
    payload["capture"]["file_name"] = source["file_name"]
    payload["import_enabled"] = True
    payload["storage"] = {
        "persisted": True,
        "scope": "local-development-content-addressed-files",
        "record_id": digest,
        "reused": reused,
        "created_at": manifest.get("created_at"),
        "source_file_name": source["file_name"],
        "official_process_summary_persisted": False,
        "official_api_published": False,
        "tenant_association_created": False,
    }
    return payload


def import_local_capture(capture_bytes: bytes, file_name: str, store_dir: Path) -> dict[str, Any]:
    if not capture_bytes or len(capture_bytes) > MAX_IMPORT_BYTES:
        raise LocalImportRejected(413, "capture_size_out_of_range")
    try:
        capture = json.loads(capture_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalImportRejected(422, "capture_must_be_utf8_json") from exc
    if not isinstance(capture, dict):
        raise LocalImportRejected(422, "capture_must_be_json_object")
    page_data = capture.get("page_data")
    lawsuit_records = [
        item for item in page_data
        if isinstance(item, dict)
        and item.get("response_type") == "lawsuit"
        and isinstance(item.get("response_data"), dict)
    ] if isinstance(page_data, list) else []
    if len(lawsuit_records) != 1:
        raise LocalImportRejected(422, "capture_requires_exactly_one_lawsuit")
    try:
        normalized = extract_promotable_fields(
            {"response_type": "lawsuit", "response_data": lawsuit_records[0]["response_data"]}
        )
    except (TypeError, ValueError) as exc:
        raise LocalImportRejected(422, "lawsuit_normalization_failed") from exc
    if int(normalized.get("secrecy_level") or 0) > 0:
        raise LocalImportRejected(422, "secret_proceedings_not_allowed")

    digest = _sha256(capture_bytes)
    root = _local_store_root(store_dir)
    record_dir = root / digest
    if record_dir.exists():
        return _stored_payload(record_dir, digest, reused=True)

    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{digest[:16]}-", dir=root))
    capture_path = temporary_dir / IMPORT_CAPTURE_NAME
    try:
        with capture_path.open("xb") as target:
            target.write(capture_bytes)
            target.flush()
            os.fsync(target.fileno())
        if os.name == "posix":
            capture_path.chmod(0o600)

        child_env = os.environ.copy()
        database_keys = {
            "DATABASE_URL", "RAGVIA_DATABASE_URL", "DB_URL", "PGHOST",
            "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "POSTGRES_PASSWORD",
        }
        for key in tuple(child_env):
            upper = key.upper()
            if (
                upper in database_keys
                or upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))
                or upper == "ANTHROPIC_APPROVAL_FILE"
            ):
                child_env.pop(key, None)
        child_env.update(
            {
                "RAGVIA_LOCAL_SUMMARY_RUNNER": "1",
                "ANTHROPIC_PAID_ENABLED": "false",
                "JUDIT_ATTACHMENTS_ENABLED": "false",
                "DATAJUD_ENABLED": "false",
                "DATAJUD_AUTHORIZED_USE": "false",
                "ALLOW_EXTERNAL_EMBEDDINGS": "false",
                "RERANKER_ENABLED": "false",
                "ALLOW_EXTERNAL_RERANKER": "false",
                "PROVIDER_MAX_ATTEMPTS": "1",
            }
        )
        runner = ROOT / "scripts" / "run_local_iasummary.py"
        try:
            completed = subprocess.run(
                [
                    sys.executable, str(runner), "--input", str(capture_path),
                    "--output-dir", str(temporary_dir),
                ],
                cwd=ROOT,
                env=child_env,
                capture_output=True,
                text=False,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalImportRejected(422, "local_extractive_run_failed") from exc
        if completed.returncode != 0:
            raise LocalImportRejected(422, "local_extractive_run_failed")

        payload = build_preview_payload(capture_path, temporary_dir)
        report = _read_json(temporary_dir / REPORT_NAME)
        if (
            report.get("source", {}).get("origin") != "existing_local_capture_file"
            or report.get("source", {}).get("acquisition_performed") is not False
            or report.get("source", {}).get("tenant_process_association") is not None
            or report.get("source", {}).get("summary_reference_used_for_generation") is not False
            or report.get("source", {}).get("summary_response_consumed") is not False
        ):
            raise LocalImportRejected(422, "local_source_provenance_invalid")
        created_at = datetime.now().astimezone().isoformat()
        manifest = {
            "schema": 1,
            "record_id": digest,
            "capture_sha256": digest,
            "created_at": created_at,
            "source": {
                "origin": "operator_supplied_existing_json_capture",
                "file_name": file_name,
                "capture_sha256": digest,
                "summary_reference_used_for_generation": False,
                "summary_response_consumed": False,
                "judit_acquisition_created": False,
                "tenant_association_created": False,
            },
            "generation": {
                "method": "rpy-local-extractive",
                "provider": None,
                "provider_calls": 0,
                "movement_count": payload["validation"]["movement_count"],
                "claim_count": payload["validation"]["claim_count"],
                "markdown_sha256": payload["markdown_sha256"],
            },
            "official_persistence": {
                "process_summaries_row_created": False,
                "api_published": False,
            },
        }
        manifest_path = temporary_dir / IMPORT_MANIFEST_NAME
        with manifest_path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            target.flush()
            os.fsync(target.fileno())
        if os.name == "posix":
            manifest_path.chmod(0o600)
        try:
            os.replace(temporary_dir, record_dir)
        except OSError:
            if not record_dir.exists():
                raise
            shutil.rmtree(temporary_dir, ignore_errors=True)
            return _stored_payload(record_dir, digest, reused=True)
        payload = _stored_payload(record_dir, digest, reused=False)
        return payload
    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


class LocalPreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        token: str,
        payload: dict[str, Any],
        port: int,
        import_store: Path | None = None,
    ) -> None:
        self.preview_token = token
        self.preview_payload = payload
        self.import_store = import_store
        self.import_lock = threading.Lock()
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

    def _authorized_local_session(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        return (
            scheme.casefold() == "bearer"
            and bool(supplied)
            and hmac.compare_digest(supplied, self.server.preview_token)
        )

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._trusted_local_request():
            self._reject(403)
            return
        if urlsplit(self.path).path != "/__local_import" or self.server.import_store is None:
            self._reject(404)
            return
        if not self._authorized_local_session():
            self._send_json(401, {"error": "local_preview_auth_required"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "application_json_required"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(411, {"error": "content_length_required"})
            return
        if content_length <= 0:
            self._send_json(400, {"error": "empty_capture"})
            return
        if content_length > MAX_IMPORT_BYTES:
            self._send_json(413, {"error": "capture_too_large"})
            return
        try:
            file_name = _safe_source_file_name(self.headers.get("X-Local-Filename", ""))
        except LocalImportRejected as exc:
            self._send_json(exc.status, {"error": exc.code})
            return
        capture_bytes = self.rfile.read(content_length)
        if len(capture_bytes) != content_length:
            self._send_json(400, {"error": "incomplete_capture_upload"})
            return
        try:
            with self.server.import_lock:
                payload = import_local_capture(
                    capture_bytes, file_name, self.server.import_store
                )
        except LocalImportRejected as exc:
            self._send_json(exc.status, {"error": exc.code})
            return
        except (OSError, subprocess.SubprocessError):
            self._send_json(500, {"error": "local_store_or_runner_failed"})
            return
        except Exception:
            self._send_json(500, {"error": "local_import_failed"})
            return
        self.server.preview_payload = payload
        self._send_json(200, payload)

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
        description="Serve the canonical frontend for a local extractive preview or an isolated loopback-only JSON import.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--import-mode", action="store_true", help="open the local file-import flow; requires --store-dir")
    mode.add_argument("--browser-smoke", action="store_true", help="verify an existing validated preview in Chromium and exit")
    mode.add_argument("--browser-import-smoke", action="store_true", help="upload --capture through the local UI, persist it, verify Chromium, and exit")
    parser.add_argument("--capture", type=Path, help="existing JSON capture (required for preview or browser-import smoke)")
    parser.add_argument("--output-dir", type=Path, help="existing local runner artifacts (required for --browser-smoke)")
    parser.add_argument("--store-dir", type=Path, help="private development-only content-addressed store, outside the checkout")
    parser.add_argument("--port", type=int, default=0, help="loopback port; 0 selects an ephemeral port")
    parser.add_argument("--open-browser", action="store_true", help="open the local authenticated session and keep serving")
    parser.add_argument("--screenshot-out", type=Path, help="private full-page screenshot path")
    parser.add_argument("--evidence-out", type=Path, help="private browser evidence JSON path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not 0 <= args.port <= 65535:
        raise SystemExit("port must be between 0 and 65535")
    if args.open_browser and (args.browser_smoke or args.browser_import_smoke):
        raise SystemExit("--open-browser cannot be combined with a browser smoke mode")

    import_mode = args.import_mode or args.browser_import_smoke
    if import_mode:
        if args.store_dir is None:
            raise SystemExit("--store-dir is required for local import mode")
        if args.output_dir is not None:
            raise SystemExit("--output-dir is only valid for the existing preview mode")
        if args.browser_import_smoke and args.capture is None:
            raise SystemExit("--capture is required for --browser-import-smoke")
        if args.import_mode and args.capture is not None:
            raise SystemExit("choose the capture interactively in --import-mode; do not pass --capture")
        try:
            store_dir = _local_store_root(args.store_dir)
        except (OSError, LocalImportRejected) as exc:
            raise SystemExit(f"local import store refused: {exc}") from exc
        payload = local_import_shell_payload()
        import_file = args.capture if args.browser_import_smoke else None
    else:
        if args.capture is None or args.output_dir is None:
            raise SystemExit("--capture and --output-dir are required for existing preview mode")
        if args.store_dir is not None:
            raise SystemExit("--store-dir is only valid in local import mode")
        try:
            payload = build_preview_payload(args.capture, args.output_dir)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"local summary preview refused: {exc}") from exc
        store_dir = None
        import_file = None

    token = secrets.token_urlsafe(32)
    server = LocalPreviewServer(token, payload, args.port, import_store=store_dir)
    origin = f"http://127.0.0.1:{server.server_port}"
    session_url = f"{origin}/#{token}"
    if args.browser_smoke or args.browser_import_smoke:
        base_dir = store_dir if import_mode else args.output_dir
        screenshot = args.screenshot_out or base_dir / "iasummary-local-preview.png"
        evidence_path = args.evidence_out or base_dir / "iasummary-local-preview-browser-evidence.json"
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            evidence = run_browser_verification(
                server, token, payload, screenshot, evidence_path, import_file=import_file
            )
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        return

    print(f"Aplicação local autenticada: {origin}/ (token temporário somente na sessão do navegador).")
    print("O processamento, o armazenamento e a visualização ficam no computador; nenhuma rota pública de processo é chamada.")
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
