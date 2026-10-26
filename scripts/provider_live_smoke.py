from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from app.datajud_client import lookup_datajud_metadata
from app.judit import normalize_cnj
from app.judit_client import (
    JuditRequestError,
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

    if "\\n" in normalized_request_id or "\\r" in normalized_request_id:
        raise RuntimeError("invalid Judit request id for private custody")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Exclusive create: never overwrite a previous paid request's identifier.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   "w", encoding="utf-8") as handle:
        handle.write(normalized_request_id + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)
    _sync_directory(path.parent)
    return True


def _sync_directory(directory: Path) -> None:
    """Make newly created directory entries durable on POSIX."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_private_request_id(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("private Judit request id file is missing or unsafe")
    identifier = path.read_text(encoding="utf-8").strip()
    if not identifier or "\\n" in identifier or "\\r" in identifier:
        raise RuntimeError("private Judit request id is incomplete")
    return identifier


def _prepare_judit_acquisition(path: Path | None, code: str) -> str | None:
    """Reserve exactly one POST attempt before the network call.

    A durable attempt without an ID means the POST outcome is UNKNOWN, never
    permission to retry. A complete ID permits GET-only resume.
    """
    if path is None:
        raise RuntimeError("private durable --judit-request-id-file is required before POST")
    if path.is_symlink():
        raise RuntimeError("private Judit request id path is unsafe")
    marker = path.with_name(path.name + ".attempt.json")
    if marker.is_symlink():
        raise RuntimeError("Judit acquisition attempt marker is unsafe")
    context = {
        "schema_version": 1,
        "cnj_sha256": _hash_identifier(normalize_cnj(code)),
        "requests_host_sha256": _hash_identifier(
            os.getenv("JUDIT_REQUESTS_BASE_URL", "https://requests.prod.judit.io").rstrip("/")
        ),
        "state": "post_outcome_may_be_unknown",
    }
    if marker.exists():
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("unreadable acquisition marker; block new POST") from exc
        if recorded != context:
            raise RuntimeError("acquisition marker does not match the CNJ/host; block new POST")
        if path.exists():
            return _read_private_request_id(path)
        raise RuntimeError("acquisition already attempted; POST outcome unknown; no retry")
    if path.exists():
        raise RuntimeError("existing Judit identifier without acquisition marker; no overwrite")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # O_EXCL acts as a cross-process one-shot claim: only one contender sends POST.
    with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   "w", encoding="utf-8") as handle:
        json.dump(context, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)
    return None


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
    """Validate access to an existing request with a single documented GET.

    GET /requests?page=1&page_size=1 is not an auth-validation endpoint:
    Judit returned HTTP 400 on both production hosts in live run 40376251694.
    Never create a request or treat a 400 as a key-validity signal.
    """
    report = _base_report("judit")
    report["auth_validated"] = False
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report

    request_id = os.getenv("PROVIDER_ACCEPTANCE_JUDIT_REQUEST_ID", "").strip()
    if not request_id:
        report["status"] = "blocked_missing_existing_request_id"
        return report
    try:
        if str(UUID(request_id)) != request_id:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError):
        report["status"] = "invalid_existing_request_id"
        return report

    report["request_id_sha256"] = _hash_identifier(request_id)
    started = perf_counter()
    try:
        status = await get_lawsuit_request_status(request_id)
    except JuditRequestError as exc:
        report.update(
            {
                "executed": True,
                "network_calls_performed": True,
                "network_call_type": "non_creating_existing_request_status_get",
                "status": "error",
                "latency_ms": round((perf_counter() - started) * 1000, 3),
                "error_class": type(exc).__name__,
                "error_code": exc.error_code,
                "http_status": exc.http_status,
                "retry_safe": exc.retry_safe,
                "provider_error_code": exc.provider_error_code,
                "provider_validation": exc.provider_validation,
            }
        )
        return report

    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "network_call_type": "non_creating_existing_request_status_get",
            "status": "ok",
            "auth_validated": True,
            "request_status": status.status,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
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

    previous_request_id = _prepare_judit_acquisition(request_id_file, code)
    if previous_request_id is not None:
        report.update({
            "status": "request_reused",
            "request_id_sha256": _hash_identifier(previous_request_id),
            "request_id_persisted": True,
            "post_calls": 0,
        })
        return report

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

    previous_request_id = _prepare_judit_acquisition(request_id_file, code)
    if previous_request_id is None:
        created = await create_lawsuit_request(code)
        request_id_persisted = _persist_judit_request_id(
            request_id_file,
            created.request_id,
        )
        request_id = created.request_id
        post_calls = 1
    else:
        request_id = previous_request_id
        request_id_persisted = True
        post_calls = 0
    report = await _observe_judit_request(request_id, post_calls=post_calls)
    report["request_id_persisted"] = request_id_persisted
    report["request_reused"] = post_calls == 0
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
        # Existing-request GET 200 validates access to that request only.
        # Do not create any new requests unless this documented GET succeeds.
        if diagnostic["status"] != "ok" or diagnostic.get("auth_validated") is not True:
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
        help="Verify access to one existing Judit request by documented GET; requires secret request ID",
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
        help="Existing Judit request id for --judit-observe; diagnostic uses environment secret only",
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
        # HTTP 400 can prove reachability, never key validity. Preserve the
        # distinct JSON status but fail the acceptance gate until verified.
        return 0 if report["status"] == "ok" and report.get("auth_validated") is True else 1

    if args.preflight_cnj:
        report = preflight_cnj(args.cnj)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "ok" else 1

    if args.judit_observe:
        try:
            observe_id = (
                _read_private_request_id(args.judit_request_id_file)
                if args.judit_request_id_file is not None
                else args.request_id
            )
            report = asyncio.run(
                _observe_judit_request(observe_id, post_calls=0)
            )
        except Exception as exc:
            report = _error_report("judit", exc)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "observed_completed" else 1

    creating_mode = args.judit_roundtrip or (
        not args.judit_observe and not args.diagnose_judit
        and args.replay_file is None and args.provider in {"judit", "both"}
    )
    if creating_mode and str(args.cnj).strip():
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true" or args.judit_request_id_file is None:
            report = _base_report("judit")
            report["status"] = "blocked_unsafe_acquisition_custody"
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 1

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
    return 0 if report["status"] in {"request_created", "request_reused"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
