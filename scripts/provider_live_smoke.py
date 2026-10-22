from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from app.datajud_client import lookup_datajud_metadata
from app.judit import normalize_cnj
from app.judit_client import (
    JuditRequestError,
    check_judit_connectivity,
    create_lawsuit_request,
    get_lawsuit_request_status,
    get_lawsuit_responses,
    judit_attachments_enabled,
)

_CAPTURE_SCHEMA_VERSION = 1


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _persist_judit_request_id(path: Path | None, request_id: str) -> bool:
    if path is None:
        return False
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        raise RuntimeError("cannot persist an empty Judit request id")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd: int | None = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(normalized_request_id + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return True


def _cnj_checksum_valid(normalized: str) -> bool:
    digits = "".join(character for character in normalized if character.isdigit())
    if len(digits) != 20:
        return False
    expected = int(digits[7:9])
    base = digits[:7] + digits[9:] + "00"
    calculated = 98 - (int(base) % 97)
    return expected == calculated


def preflight_cnj(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    report: dict[str, Any] = {
        "provider": "local",
        "executed": True,
        "network_calls_performed": False,
        "status": "invalid_cnj",
        "format_valid": False,
        "checksum_valid": False,
        "digit_count": sum(character.isdigit() for character in raw),
        "input_form": "empty",
        "justice_code": None,
        "tribunal_code": None,
    }
    if not raw:
        return report
    report["input_form"] = "digits" if raw.isdigit() else "canonical_or_other"
    try:
        normalized = normalize_cnj(raw)
    except ValueError:
        return report

    digits = "".join(character for character in normalized if character.isdigit())
    report.update(
        {
            "format_valid": True,
            "checksum_valid": _cnj_checksum_valid(normalized),
            "digit_count": len(digits),
            "input_form": "digits" if raw.isdigit() else "canonical",
            "justice_code": digits[13],
            "tribunal_code": digits[14:16],
        }
    )
    report["status"] = "ok" if report["checksum_valid"] else "invalid_checksum"
    return report


def _base_report(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "executed": False,
        "network_calls_performed": False,
        "status": "not_started",
        "latency_ms": None,
    }


def _error_report(provider: str, exc: Exception) -> dict[str, Any]:
    report = _base_report(provider)
    report.update(
        {
            "status": "error",
            "error_class": type(exc).__name__,
        }
    )
    if isinstance(exc, JuditRequestError):
        report["error_code"] = exc.error_code
        report["http_status"] = exc.http_status
        report["retry_safe"] = exc.retry_safe
        report["provider_error_code"] = exc.provider_error_code
        report["provider_validation"] = exc.provider_validation
        report["network_call_attempted"] = True
    return report


def _capture_document(*, code: str, report: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_cnj(code)
    return {
        "schema_version": _CAPTURE_SCHEMA_VERSION,
        "kind": "provider_acceptance_capture",
        "request": {
            "provider": report.get("provider"),
            "cnj_sha256": _hash_identifier(normalized),
            "judit": {
                "operation": "lawsuit_cnj",
                "with_attachments": False,
            },
            "datajud": {
                "operation": "public_cnj_lookup",
                "secrecy_level": 0,
            },
        },
        "response": report,
    }


def write_capture(path: Path, *, code: str, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = _capture_document(code=code, report=report)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_replay(path: Path, *, code: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("provider replay capture must be a JSON object")
    if document.get("schema_version") != _CAPTURE_SCHEMA_VERSION:
        raise RuntimeError("unsupported provider replay capture schema")
    if document.get("kind") != "provider_acceptance_capture":
        raise RuntimeError("invalid provider replay capture kind")

    request = document.get("request")
    response = document.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise RuntimeError("provider replay capture is incomplete")

    expected_hash = _hash_identifier(normalize_cnj(code))
    if request.get("cnj_sha256") != expected_hash:
        raise RuntimeError("provider replay capture does not match the requested CNJ")

    replay = dict(response)
    replay["executed"] = True
    replay["network_calls_performed"] = False
    replay["replayed"] = True
    return replay


async def diagnose_judit() -> dict[str, Any]:
    report = _base_report("judit")
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report

    started = perf_counter()
    try:
        await check_judit_connectivity()
    except JuditRequestError as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        reachable_unverified = (
            exc.http_status == 400
            and exc.provider_error_code == "HttpBadRequestError"
        )
        report.update(
            {
                "executed": True,
                "network_calls_performed": True,
                "network_call_type": "non_creating_reachability_probe",
                "status": "reachable_unverified" if reachable_unverified else "error",
                "latency_ms": round(elapsed_ms, 3),
                "error_class": type(exc).__name__,
                "error_code": exc.error_code,
                "http_status": exc.http_status,
                "retry_safe": exc.retry_safe,
                "provider_error_code": exc.provider_error_code,
                "provider_validation": exc.provider_validation,
                "auth_validated": False,
            }
        )
        return report

    elapsed_ms = (perf_counter() - started) * 1000
    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "network_call_type": "non_creating_reachability_probe",
            "status": "ok",
            "auth_validated": True,
            "latency_ms": round(elapsed_ms, 3),
        }
    )
    return report


async def _smoke_judit(
    code: str,
    *,
    request_id_file: Path | None = None,
) -> dict[str, Any]:
    report = _base_report("judit")
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report
    if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
        report["status"] = "skipped_missing_authorization"
        return report
    if judit_attachments_enabled():
        raise RuntimeError("Judit live smoke requires JUDIT_ATTACHMENTS_ENABLED=false")

    started = perf_counter()
    result = await create_lawsuit_request(code)
    request_id_persisted = _persist_judit_request_id(
        request_id_file,
        result.request_id,
    )
    elapsed_ms = (perf_counter() - started) * 1000
    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "status": "request_created",
            "latency_ms": round(elapsed_ms, 3),
            "request_id_sha256": _hash_identifier(result.request_id),
            "request_id_persisted": request_id_persisted,
        }
    )
    return report


async def _observe_judit_request(
    request_id: str,
    *,
    post_calls: int,
) -> dict[str, Any]:
    report = _base_report("judit")
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report
    if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
        report["status"] = "skipped_missing_authorization"
        return report
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        report["status"] = "skipped_missing_request_id"
        return report

    started = perf_counter()
    request_id_hash = _hash_identifier(normalized_request_id)
    poll_interval = 3.0
    max_attempts = 40
    final_status = "unknown"
    response_status = None
    attempts = 0
    responses = None

    for attempts in range(1, max_attempts + 1):
        status_result = await get_lawsuit_request_status(normalized_request_id)
        final_status = status_result.status
        responses = await get_lawsuit_responses(normalized_request_id)
        response_status = responses.request_status
        terminal_status = response_status or final_status
        if terminal_status == "completed":
            break
        if terminal_status in {"failed", "error", "cancelled", "canceled"}:
            break
        await asyncio.sleep(poll_interval)

    if responses is None:
        responses = await get_lawsuit_responses(normalized_request_id)

    elapsed_ms = (perf_counter() - started) * 1000
    effective_status = response_status or final_status
    has_process_payload = (
        responses.lawsuit_response_count > 0
        or responses.direct_payload_count > 0
    )
    if (
        effective_status == "completed"
        and responses.response_count > 0
        and has_process_payload
        and responses.application_error_count == 0
    ):
        status = "observed_completed"
    elif responses.application_error_count > 0:
        status = "observed_application_error"
    elif effective_status == "completed":
        status = "observed_completed_without_process"
    elif has_process_payload:
        status = "observed_payload_received_pending_completion"
    else:
        status = "observed_pending"

    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "status": status,
            "latency_ms": round(elapsed_ms, 3),
            "request_id_sha256": request_id_hash,
            "request_status": final_status,
            "responses_request_status": response_status,
            "effective_request_status": effective_status,
            "status_poll_attempts": attempts,
            "response_count": responses.response_count,
            "lawsuit_response_count": responses.lawsuit_response_count,
            "application_info_count": responses.application_info_count,
            "application_error_count": responses.application_error_count,
            "application_error_code": responses.application_error_code,
            "application_error_message": responses.application_error_message,
            "other_response_count": responses.other_response_count,
            "direct_payload_count": responses.direct_payload_count,
            "post_calls": post_calls,
            "get_calls": attempts * 2,
        }
    )
    return report


async def _smoke_judit_roundtrip(
    code: str,
    *,
    request_id_file: Path | None = None,
) -> dict[str, Any]:
    if judit_attachments_enabled():
        raise RuntimeError("Judit round-trip smoke requires JUDIT_ATTACHMENTS_ENABLED=false")
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report = _base_report("judit")
        report["status"] = "skipped_missing_credentials"
        return report
    if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
        report = _base_report("judit")
        report["status"] = "skipped_missing_authorization"
        return report

    created = await create_lawsuit_request(code)
    request_id_persisted = _persist_judit_request_id(
        request_id_file,
        created.request_id,
    )
    report = await _observe_judit_request(created.request_id, post_calls=1)
    report["request_id_persisted"] = request_id_persisted
    observed_status = str(report.get("status") or "")
    report["status"] = {
        "observed_completed": "roundtrip_completed",
        "observed_payload_received_pending_completion": (
            "roundtrip_payload_received_pending_completion"
        ),
        "observed_application_error": "roundtrip_application_error",
        "observed_completed_without_process": "roundtrip_completed_without_process",
        "observed_pending": "roundtrip_pending",
    }.get(observed_status, "roundtrip_incomplete")
    return report


async def _smoke_datajud(code: str) -> dict[str, Any]:
    report = _base_report("datajud")
    if not _env_bool("DATAJUD_ENABLED", False):
        report["status"] = "skipped_disabled"
        return report
    if not _env_bool("DATAJUD_AUTHORIZED_USE", False):
        report["status"] = "skipped_missing_authorization"
        return report
    if not os.getenv("DATAJUD_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report
    if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
        report["status"] = "skipped_missing_authorization"
        return report

    started = perf_counter()
    result = await lookup_datajud_metadata(code=code, secrecy_level=0)
    elapsed_ms = (perf_counter() - started) * 1000
    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "status": result.status,
            "latency_ms": round(elapsed_ms, 3),
            "metadata_present": result.metadata is not None,
            "error_code": result.error_code,
        }
    )
    return report


async def run_smoke(
    provider: str,
    code: str,
    *,
    judit_request_id_file: Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_cnj(code)
    if provider == "judit":
        if judit_request_id_file is None:
            return await _smoke_judit(normalized)
        return await _smoke_judit(
            normalized,
            request_id_file=judit_request_id_file,
        )
    if provider == "datajud":
        return await _smoke_datajud(normalized)
    if provider == "both":
        report = _base_report("both")
        if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
            report["status"] = "skipped_missing_authorization"
            return report
        if judit_attachments_enabled():
            raise RuntimeError("Judit live smoke requires JUDIT_ATTACHMENTS_ENABLED=false")
        if not _env_bool("DATAJUD_ENABLED", False):
            report["status"] = "skipped_disabled"
            return report
        if not _env_bool("DATAJUD_AUTHORIZED_USE", False):
            report["status"] = "skipped_missing_authorization"
            return report
        if (
            not os.getenv("JUDIT_API_KEY", "").strip()
            or not os.getenv("DATAJUD_API_KEY", "").strip()
        ):
            report["status"] = "skipped_missing_credentials"
            return report

        diagnostic = await diagnose_judit()
        if diagnostic["status"] not in {"ok", "reachable_unverified"}:
            return {
                "provider": "both",
                "executed": False,
                "network_calls_performed": False,
                "status": "blocked_judit_diagnostic",
                "diagnostic": diagnostic,
            }

        judit_call = (
            _smoke_judit(normalized)
            if judit_request_id_file is None
            else _smoke_judit(
                normalized,
                request_id_file=judit_request_id_file,
            )
        )
        judit, datajud = await asyncio.gather(
            judit_call,
            _smoke_datajud(normalized),
        )
        return {
            "provider": "both",
            "executed": bool(judit["executed"] and datajud["executed"]),
            "network_calls_performed": bool(
                judit["network_calls_performed"] and datajud["network_calls_performed"]
            ),
            "status": "ok"
            if (
                judit["status"] == "request_created"
                and datajud["status"] in {"ok", "not_found"}
            )
            else "incomplete",
            "results": {
                "judit": judit,
                "datajud": datajud,
            },
        }
    raise ValueError(f"unsupported provider: {provider}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one controlled live provider smoke or replay a sanitized capture "
            "against an explicitly authorized CNJ."
        )
    )
    parser.add_argument("--provider", choices=("judit", "datajud", "both"), default="both")
    parser.add_argument(
        "--diagnose-judit",
        action="store_true",
        help="Validate Judit API-key connectivity with a non-creating GET and exit",
    )
    parser.add_argument(
        "--judit-roundtrip",
        action="store_true",
        help="Create one Judit request, poll status by GET, and fetch sanitized response metadata",
    )
    parser.add_argument(
        "--judit-observe",
        action="store_true",
        help="Observe an existing Judit request by GET only; never creates a provider request",
    )
    parser.add_argument(
        "--request-id",
        default=os.getenv("PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID", ""),
        help="Existing Judit request id for --judit-observe; prefer environment injection",
    )
    parser.add_argument(
        "--judit-request-id-file",
        type=Path,
        default=(
            Path(os.environ["PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID_FILE"])
            if os.getenv("PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID_FILE", "").strip()
            else None
        ),
        help=(
            "Persist the raw request id to a private file immediately after Judit "
            "creates the request; stdout/captures remain hashed"
        ),
    )
    parser.add_argument(
        "--preflight-cnj",
        action="store_true",
        help="Validate CNJ format/check digits locally and exit without network calls",
    )
    parser.add_argument(
        "--cnj",
        default=os.getenv("PROVIDER_ACCEPTANCE_CNJ", ""),
        help="Explicitly authorized CNJ; defaults to PROVIDER_ACCEPTANCE_CNJ",
    )
    parser.add_argument(
        "--capture-file",
        type=Path,
        default=None,
        help="Write a sanitized request/response capture after a successful live execution",
    )
    parser.add_argument(
        "--replay-file",
        type=Path,
        default=None,
        help="Replay a prior sanitized capture without provider network calls",
    )
    args = parser.parse_args()

    if args.diagnose_judit:
        report = asyncio.run(diagnose_judit())
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] in {"ok", "reachable_unverified"} else 1

    if args.preflight_cnj:
        report = preflight_cnj(args.cnj)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "ok" else 1

    if args.judit_observe:
        try:
            report = asyncio.run(
                _observe_judit_request(args.request_id, post_calls=0)
            )
        except Exception as exc:
            report = _error_report("judit", exc)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "observed_completed" else 1

    if args.judit_roundtrip:
        if not str(args.cnj).strip():
            report = _base_report("judit")
            report["status"] = "skipped_missing_cnj"
        else:
            try:
                report = asyncio.run(
                    _smoke_judit_roundtrip(
                        normalize_cnj(args.cnj),
                        request_id_file=args.judit_request_id_file,
                    )
                )
                if args.capture_file is not None and report.get("executed"):
                    write_capture(args.capture_file, code=args.cnj, report=report)
            except Exception as exc:
                report = _error_report("judit", exc)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "roundtrip_completed" else 1

    if not str(args.cnj).strip():
        report = _base_report(args.provider)
        report["status"] = "skipped_missing_cnj"
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    try:
        if args.replay_file is not None:
            report = load_replay(args.replay_file, code=args.cnj)
        else:
            if args.judit_request_id_file is None:
                report = asyncio.run(run_smoke(args.provider, args.cnj))
            else:
                report = asyncio.run(
                    run_smoke(
                        args.provider,
                        args.cnj,
                        judit_request_id_file=args.judit_request_id_file,
                    )
                )
            if args.capture_file is not None and report.get("executed"):
                write_capture(args.capture_file, code=args.cnj, report=report)
    except Exception as exc:
        report = _error_report(args.provider, exc)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "error":
        return 1
    if not report["executed"]:
        return 0
    if args.provider == "both":
        return 0 if report["status"] == "ok" else 1
    if args.provider == "datajud":
        return 0 if report["status"] in {"ok", "not_found"} else 1
    return 0 if report["status"] == "request_created" else 1


if __name__ == "__main__":
    raise SystemExit(main())
