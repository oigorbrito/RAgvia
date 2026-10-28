from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import run_local_iasummary as local_summary_runner
from scripts.serve_local_iasummary_preview import (
    MARKDOWN_NAME,
    MAX_IMPORT_BYTES,
    REPORT_NAME,
    LocalImportRejected,
    LocalPreviewServer,
    _safe_source_file_name,
    build_preview_payload,
    import_local_capture,
    local_import_shell_payload,
)


def _synthetic_artifacts(tmp_path: Path) -> tuple[Path, Path, dict]:
    output_dir = tmp_path / "private-output"
    output_dir.mkdir()
    capture_path = tmp_path / "synthetic-capture.json"
    steps = [
        {"date": f"2026-09-{index + 1:02d}T12:00:00Z", "content": f"synthetic movement {index + 1}"}
        for index in range(13)
    ]
    capture = {
        "page_data": [
            {
                "response_type": "lawsuit",
                "created_at": "2026-09-02T18:51:00.225Z",
                "response_data": {
                    "code": "0000000-00.2026.8.21.0001",
                    "tribunal_acronym": "TJRS",
                    "secrecy_level": 0,
                    "steps": steps,
                },
            },
            {"response_type": "summary", "response_data": {"data": "reference excluded"}},
        ]
    }
    capture_bytes = json.dumps(capture, ensure_ascii=False).encode("utf-8")
    capture_path.write_bytes(capture_bytes)
    movement_sources = [
        {
            "source_ref": f"steps/{index}",
            "evidence_ref": f"m-{index}",
            "source_text_sha256": hashlib.sha256(f"synthetic movement {index + 1}".encode()).hexdigest(),
        }
        for index in range(13)
    ]
    claim_refs = [
        {"claim_id": "synthesis", "evidence_refs": ["p-local", "m-12"]},
        {"claim_id": "current_status", "evidence_refs": ["m-12"]},
        *[
            {"claim_id": f"timeline:{index}", "evidence_refs": [f"m-{index}"]}
            for index in range(13)
        ],
        *[
            {"claim_id": f"attention:{index}", "evidence_refs": ["p-local"]}
            for index in range(6)
        ],
    ]
    report = {
        "source": {
            "sha256": hashlib.sha256(capture_bytes).hexdigest(),
            "summary_reference_excluded_from_generation": True,
            "movement_count": 13,
            "secrecy_level": 0,
            "normalized_classifications": [
                {"name": "Synthetic class", "suspect": False}
            ],
        },
        "generation": {
            "model": "rpy-local-extractive",
            "mode": "local_extractive_no_provider",
            "anthropic_called": False,
            "persisted": False,
        },
        "retrieval": {"selected_movement_count": 13},
        "validation": {
            "passed": True,
            "errors": [],
            "claim_count": 21,
            "timeline_claim_count": 13,
            "all_movements_have_structured_evidence_refs": True,
        },
        "provenance": {
            "movement_sources": movement_sources,
            "claim_evidence_refs": claim_refs,
        },
    }
    (output_dir / REPORT_NAME).write_text(json.dumps(report), encoding="utf-8")
    (output_dir / MARKDOWN_NAME).write_text(
        "# Local report\n\n## Documento gerado\n\n"
        "# Resumo do processo\n\n## Andamento\n- Synthetic only.\n\n"
        "## Validação factual e proveniência\n- fixture\n",
        encoding="utf-8",
    )
    return capture_path, output_dir, report


def test_preview_reads_existing_validated_lawsuit_and_hashes_only_display_markdown(tmp_path: Path) -> None:
    capture_path, output_dir, _report = _synthetic_artifacts(tmp_path)

    payload = build_preview_payload(capture_path, output_dir)

    assert payload["mode"] == "extrativo_local"
    assert payload["published"] is False
    assert payload["process"]["code"] == "0000000-00.2026.8.21.0001"
    assert payload["capture"]["file_name"] == capture_path.name
    assert payload["validation"]["movement_count"] == 13
    assert payload["validation"]["selected_movement_count"] == 13
    assert payload["validation"]["claim_count"] == 21
    assert payload["validation"]["timeline_claim_count"] == 13
    assert payload["validation"]["evidence_complete"] is True
    assert len(payload["movements"]) == 13
    assert payload["movements"][0]["text"] == "synthetic movement 1"
    assert payload["movements"][0]["source_ref"] == "steps/0"
    assert payload["movements"][0]["evidence_ref"] == "m-0"
    assert payload["storage"]["persisted"] is False
    assert payload["generation"]["anthropic_called"] is False
    assert payload["generation"]["persisted"] is False
    assert payload["markdown"] == "# Resumo do processo\n\n## Andamento\n- Synthetic only."
    assert payload["markdown_sha256"] == hashlib.sha256(payload["markdown"].encode()).hexdigest()


def test_import_shell_contains_no_process_data_and_filename_is_basename_only() -> None:
    payload = local_import_shell_payload()

    assert payload["mode"] == "local_import"
    assert payload["published"] is False
    assert "process" not in payload
    assert "movements" not in payload
    assert _safe_source_file_name("manager-capture.json") == "manager-capture.json"
    with pytest.raises(LocalImportRejected, match="invalid_file_name"):
        _safe_source_file_name("..%2Fprivate.json")


def test_local_import_rejects_oversized_input_before_json_parsing(tmp_path: Path) -> None:
    store = tmp_path / "isolated-store"

    with pytest.raises(LocalImportRejected, match="capture_size_out_of_range"):
        import_local_capture(b"{" + b" " * MAX_IMPORT_BYTES, "large.json", store)

    assert not store.exists()


def test_local_import_rejects_non_lawsuit_capture_before_creating_storage(tmp_path: Path) -> None:
    store = tmp_path / "isolated-store"
    summary_only = json.dumps({"page_data": [{"response_type": "summary", "response_data": {}}]}).encode()

    with pytest.raises(LocalImportRejected, match="capture_requires_exactly_one_lawsuit"):
        import_local_capture(summary_only, "reference.json", store)

    assert not store.exists()


def test_local_import_rejects_a_store_inside_the_checkout(tmp_path: Path) -> None:
    # Validate the store boundary after lawsuit normalization but before any write or generation.
    from scripts.serve_local_iasummary_preview import ROOT

    capture_path, _output_dir, _report = _synthetic_artifacts(tmp_path)
    with pytest.raises(LocalImportRejected, match="store_must_be_outside_checkout"):
        import_local_capture(
            capture_path.read_bytes(), "operator-capture.json", ROOT / "tests" / "temporary-local-store"
        )


def test_local_runner_never_consumes_the_summary_response(tmp_path: Path, monkeypatch) -> None:
    sentinel = "SUMMARY_RESPONSE_MUST_NOT_ENTER_LOCAL_GENERATION"
    capture_path = tmp_path / "operator-capture.json"
    capture_path.write_text(
        json.dumps(
            {
                "page_data": [
                    {
                        "response_type": "lawsuit",
                        "created_at": "2026-09-02T18:51:00.225Z",
                        "response_data": {
                            "code": "0000000-00.2026.8.21.0001",
                            "secrecy_level": 0,
                        },
                    },
                    {"response_type": "summary", "response_data": {"data": sentinel}},
                ]
            }
        ),
        encoding="utf-8",
    )
    received_lawsuit_records: list[dict] = []
    received_generation_contexts: list[dict] = []

    async def capture_context(record, **_kwargs):
        assert record["response_type"] == "lawsuit"
        received_lawsuit_records.append(record)
        return {
            "code": record["response_data"]["code"],
            "header": {},
            "parties": [],
            "representatives": [],
            "secrecy_level": 0,
            "_selected_sources": [],
            "_glossary_sources": [],
            "legal_knowledge": [],
        }, []

    async def fake_generation(*, local_context):
        received_generation_contexts.append(local_context)
        return {
            "structured_output": {"summary": {"claims": []}},
            "markdown": "# Resumo do processo\n\nResultado de teste isolado.",
            "model": "rpy-local-extractive",
            "persisted": False,
            "reused": False,
            "generation_ms": 1,
            "validation": {"passed": True, "errors": []},
        }

    monkeypatch.setattr(local_summary_runner, "_build_lawsuit_context", capture_context)
    monkeypatch.setattr(local_summary_runner.rag, "generate_summary", fake_generation)
    monkeypatch.setattr(local_summary_runner.rag, "anthropic_client", None, raising=False)
    monkeypatch.setenv("RAGVIA_LOCAL_SUMMARY_RUNNER", "0")
    output_dir = tmp_path / "output"

    exit_code = asyncio.run(
        local_summary_runner._run(
            SimpleNamespace(input=capture_path, output_dir=output_dir)
        )
    )

    report = json.loads((output_dir / REPORT_NAME).read_text(encoding="utf-8"))
    markdown = (output_dir / MARKDOWN_NAME).read_text(encoding="utf-8")
    assert exit_code == 0
    assert len(received_lawsuit_records) == len(received_generation_contexts) == 1
    assert sentinel not in json.dumps(received_lawsuit_records, ensure_ascii=False)
    assert sentinel not in json.dumps(received_generation_contexts, ensure_ascii=False)
    assert sentinel not in markdown
    assert report["source"]["summary_response_consumed"] is False
    assert report["source"]["summary_reference_excluded_from_generation"] is True
    assert "comparison" not in report


def test_preview_fails_closed_if_capture_no_longer_matches_validation_report(tmp_path: Path) -> None:
    capture_path, output_dir, _report = _synthetic_artifacts(tmp_path)
    capture_path.write_text('{"changed": true}', encoding="utf-8")

    with pytest.raises(ValueError, match="capture hash does not match"):
        build_preview_payload(capture_path, output_dir)


def test_local_import_post_is_bearer_and_origin_protected_and_rejects_summary_only(tmp_path: Path) -> None:
    token = uuid4().hex
    store = tmp_path / "local-imports"
    server = LocalPreviewServer(token, local_import_shell_payload(), 0, import_store=store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        unauthorized = urllib.request.Request(
            f"{origin}/__local_import",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Local-Filename": "capture.json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as auth_error:
            urllib.request.urlopen(unauthorized, timeout=5)
        assert auth_error.value.code == 401

        untrusted = urllib.request.Request(
            f"{origin}/__local_import",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Local-Filename": "capture.json",
                "Origin": "http://untrusted.invalid",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as origin_error:
            urllib.request.urlopen(untrusted, timeout=5)
        assert origin_error.value.code == 403

        summary_only = json.dumps({"page_data": [{"response_type": "summary", "response_data": {}}]}).encode()
        authorized = urllib.request.Request(
            f"{origin}/__local_import",
            data=summary_only,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Local-Filename": "capture.json",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as invalid_capture:
            urllib.request.urlopen(authorized, timeout=5)
        assert invalid_capture.value.code == 422
        assert json.loads(invalid_capture.value.read()) == {
            "error": "capture_requires_exactly_one_lawsuit"
        }
        assert not store.exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_preview_server_is_loopback_bound_and_requires_local_bearer_session(tmp_path: Path) -> None:
    capture_path, output_dir, _report = _synthetic_artifacts(tmp_path)
    payload = build_preview_payload(capture_path, output_dir)
    token = uuid4().hex
    server = LocalPreviewServer(token, payload, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        assert server.server_address[0] == "127.0.0.1"
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(f"{origin}/__local_preview_payload", timeout=5)
        assert unauthorized.value.code == 401

        denied = urllib.request.Request(
            f"{origin}/__local_preview_payload",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://untrusted.invalid"},
        )
        with pytest.raises(urllib.error.HTTPError) as untrusted_origin:
            urllib.request.urlopen(denied, timeout=5)
        assert untrusted_origin.value.code == 403

        authorized = urllib.request.Request(
            f"{origin}/__local_preview_payload",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(authorized, timeout=5) as response:
            returned = json.loads(response.read().decode("utf-8"))
        assert returned["mode"] == "extrativo_local"
        assert returned["published"] is False

        index = urllib.request.urlopen(f"{origin}/", timeout=5).read().decode("utf-8")
        assert 'src="__local_preview_bootstrap.js" defer' in index
        assert 'src="app.js" defer' not in index
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
