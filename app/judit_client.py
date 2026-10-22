from __future__ import annotations

import asyncio
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.json_utils import loads_strict_json

JUDIT_REQUESTS_BASE_URL = "https://requests.prod.judit.io"
JUDIT_REQUESTS_COMPAT_BASE_URL = "https://requests.production.judit.io"
JUDIT_REQUESTS_URL = f"{JUDIT_REQUESTS_BASE_URL}/requests/"
JUDIT_TRACKING_URL = "https://tracking.production.judit.io/tracking"
JUDIT_LAWSUITS_URL = "https://lawsuits.production.judit.io/lawsuits"
_MAX_RESPONSE_BYTES = 262144
_DEFAULT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024


class JuditRequestError(RuntimeError):
    """Safe provider-boundary failure with allowlisted diagnostic metadata only."""

    def __init__(
        self,
        message: str,
        *,
        retry_safe: bool = False,
        error_code: str = "provider_error",
        http_status: int | None = None,
        provider_error_code: str | None = None,
        provider_validation: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_safe = retry_safe
        self.error_code = error_code
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider_validation = provider_validation or []


@dataclass(frozen=True, slots=True)
class JuditRequestResult:
    request_id: str


@dataclass(frozen=True, slots=True)
class JuditTrackingResult:
    tracking_id: str
    status: str


@dataclass(frozen=True, slots=True)
class JuditRequestStatusResult:
    status: str


@dataclass(frozen=True, slots=True)
class JuditResponsesResult:
    request_status: str | None
    response_count: int
    lawsuit_response_count: int
    application_info_count: int
    application_error_count: int
    other_response_count: int
    direct_payload_count: int
    application_error_code: int | None
    application_error_message: str | None


@dataclass(frozen=True, slots=True)
class JuditAttachmentDownload:
    content_type: str
    data: bytes


def judit_attachments_enabled() -> bool:
    raw = os.environ.get("JUDIT_ATTACHMENTS_ENABLED", "false").strip().lower()
    if raw in {"", "0", "false", "no", "off"}:
        return False
    if raw not in {"1", "true", "yes", "on"}:
        raise RuntimeError("JUDIT_ATTACHMENTS_ENABLED must be a boolean")
    mode = os.environ.get("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "").strip().lower()
    if mode != "direct_api_key":
        raise RuntimeError(
            "JUDIT_ATTACHMENT_DOWNLOAD_MODE must be direct_api_key when attachments are enabled"
        )
    return True


def _timeout_seconds() -> float:
    raw = os.environ.get("JUDIT_TIMEOUT_SECONDS", "15")
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("JUDIT_TIMEOUT_SECONDS must be numeric") from exc
    if not math.isfinite(value) or not 0 < value <= 60:
        raise RuntimeError(
            "JUDIT_TIMEOUT_SECONDS must be a finite number between 0 and 60"
        )
    return value


def _attachment_max_bytes() -> int:
    raw = os.environ.get("ATTACHMENT_MAX_BYTES", str(_DEFAULT_ATTACHMENT_MAX_BYTES))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("ATTACHMENT_MAX_BYTES must be an integer") from exc
    if value <= 0:
        raise RuntimeError("ATTACHMENT_MAX_BYTES must be greater than zero")
    return value


def _api_key() -> str:
    value = os.environ.get("JUDIT_API_KEY", "").strip()
    if not value:
        raise RuntimeError("JUDIT_API_KEY is required")
    return value


def _requests_base_url() -> str:
    value = os.environ.get("JUDIT_REQUESTS_BASE_URL", JUDIT_REQUESTS_BASE_URL).strip().rstrip("/")
    if value not in {JUDIT_REQUESTS_BASE_URL, JUDIT_REQUESTS_COMPAT_BASE_URL}:
        raise RuntimeError(
            "JUDIT_REQUESTS_BASE_URL must be an approved Judit requests host"
        )
    return value


def _requests_url(path: str = "") -> str:
    base = _requests_base_url()
    suffix = path.lstrip("/")
    return f"{base}/{suffix}" if suffix else base


def _callback_url() -> str | None:
    value = os.environ.get("JUDIT_CALLBACK_URL", "").strip()
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("JUDIT_CALLBACK_URL must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError("JUDIT_CALLBACK_URL must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise RuntimeError("JUDIT_CALLBACK_URL must not contain query parameters or fragments")
    return value


_SAFE_PROVIDER_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _safe_http_error_metadata(
    exc: urllib.error.HTTPError,
) -> tuple[str | None, list[dict[str, str]]]:
    """Extract only short allowlisted machine metadata from an HTTP error body."""
    fp = getattr(exc, "fp", None)
    if fp is None:
        return None, []
    try:
        raw = fp.read(8193)
    except (AttributeError, OSError):
        return None, []
    if not raw or len(raw) > 8192:
        return None, []
    try:
        body: Any = loads_strict_json(raw)
    except (TypeError, ValueError):
        return None, []
    if not isinstance(body, dict):
        return None, []

    candidates: list[Any] = [
        body.get("code"),
        body.get("error_code"),
    ]
    validation: list[dict[str, str]] = []
    error = body.get("error")
    if isinstance(error, dict):
        candidates.extend([error.get("code"), error.get("name")])
        data = error.get("data")
        if isinstance(data, str):
            candidates.append(data)
        elif isinstance(data, list):
            for item in data[:10]:
                if not isinstance(item, dict):
                    continue
                safe_item: dict[str, str] = {}
                for key in ("field", "rule"):
                    value = item.get(key)
                    if isinstance(value, str):
                        normalized = value.strip()
                        if _SAFE_PROVIDER_CODE_RE.fullmatch(normalized):
                            safe_item[key] = normalized
                if safe_item:
                    validation.append(safe_item)
    elif isinstance(error, str):
        candidates.append(error)

    provider_code = None
    for candidate in candidates:
        if isinstance(candidate, str):
            value = candidate.strip()
            if _SAFE_PROVIDER_CODE_RE.fullmatch(value):
                provider_code = value
                break
    return provider_code, validation


def _provider_request(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    accepted_statuses: set[int],
    not_found_is_success: bool = False,
) -> dict[str, Any] | None:
    data = (
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "api-key": _api_key(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            if response.status not in accepted_statuses:
                raise JuditRequestError(
                    f"Judit request failed with HTTP {response.status}",
                    error_code=f"http_{response.status}",
                    http_status=int(response.status),
                )
            if response.status == 204:
                return None
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise JuditRequestError(
                "Judit response exceeded safe size",
                error_code="response_too_large",
            )
    except urllib.error.HTTPError as exc:
        if not_found_is_success and exc.code == 404:
            return None
        # A normal 4xx response is an explicit rejection and can be retried only
        # after an operator/user changes or explicitly repeats the request. 5xx,
        # 408, 429 and transport failures remain ambiguous.
        provider_error_code, provider_validation = _safe_http_error_metadata(exc)
        raise JuditRequestError(
            f"Judit request failed with HTTP {exc.code}",
            retry_safe=400 <= exc.code < 500 and exc.code not in {408, 429},
            error_code=f"http_{exc.code}",
            http_status=int(exc.code),
            provider_error_code=provider_error_code,
            provider_validation=provider_validation,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JuditRequestError(
            "Judit request failed",
            error_code="transport_error",
        ) from None

    if not raw:
        return None
    try:
        body: Any = loads_strict_json(raw)
    except ValueError:
        raise JuditRequestError(
            "Judit returned an invalid response",
            error_code="invalid_response",
        ) from None
    if not isinstance(body, dict):
        raise JuditRequestError(
            "Judit returned an invalid response",
            error_code="invalid_response",
        )
    return body


def _attachment_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Type") if headers is not None else None
    normalized = str(value or "application/octet-stream").split(";", 1)[0].strip().lower()
    return normalized or "application/octet-stream"


class _RejectAttachmentRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_attachment_response(request: urllib.request.Request, *, timeout: float):
    """Do not forward the authenticated attachment request to a redirect target."""
    opener = urllib.request.build_opener(_RejectAttachmentRedirect())
    return opener.open(request, timeout=timeout)


def _download_attachment_sync(
    code: str,
    instance: str | int,
    attachment_id: str,
) -> JuditAttachmentDownload:
    normalized_code = str(code).strip()
    normalized_instance = str(instance).strip()
    normalized_attachment_id = str(attachment_id).strip()
    if not normalized_code:
        raise ValueError("process code is required")
    if not normalized_instance or "/" in normalized_instance:
        raise ValueError("lawsuit instance is required")
    if not normalized_attachment_id:
        raise ValueError("attachment_id is required")

    url = (
        f"{JUDIT_LAWSUITS_URL}/"
        f"{urllib.parse.quote(normalized_code, safe='')}/"
        f"{urllib.parse.quote(normalized_instance, safe='')}/attachments/"
        f"{urllib.parse.quote(normalized_attachment_id, safe='')}"
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/octet-stream,*/*",
            "api-key": _api_key(),
        },
    )
    max_bytes = _attachment_max_bytes()
    try:
        with _open_attachment_response(request, timeout=_timeout_seconds()) as response:
            if response.status != 200:
                raise JuditRequestError(
                    f"Judit attachment download failed with HTTP {response.status}"
                )
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise JuditRequestError("Judit attachment exceeded safe size")
            content_type = _attachment_content_type(response)
    except urllib.error.HTTPError as exc:
        raise JuditRequestError(
            f"Judit attachment download failed with HTTP {exc.code}",
            retry_safe=400 <= exc.code < 500 and exc.code not in {408, 429},
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JuditRequestError("Judit attachment download failed") from None

    return JuditAttachmentDownload(content_type=content_type, data=data)


def _check_connectivity_sync() -> dict[str, Any]:
    """Probe Judit reachability without creating a paid lawsuit request.

    This collection-style GET is not a documented auth-validation endpoint.
    Callers must not treat HTTP 400 as proof that the API key is invalid.
    """
    body = _provider_request(
        f"{_requests_url('requests')}?page=1&page_size=1",
        method="GET",
        accepted_statuses={200},
    )
    return body or {}


async def check_judit_connectivity() -> dict[str, Any]:
    return await asyncio.to_thread(_check_connectivity_sync)


def _create_request_sync(code: str) -> JuditRequestResult:
    payload: dict[str, Any] = {
        "search": {
            "search_type": "lawsuit_cnj",
            "search_key": code,
        },
        "with_attachments": judit_attachments_enabled(),
    }
    callback_url = _callback_url()
    if callback_url is not None:
        payload["callback_url"] = callback_url

    body = _provider_request(
        _requests_url("requests/"),
        method="POST",
        payload=payload,
        accepted_statuses={201},
    )
    request_id = body.get("request_id") if body else None
    if not isinstance(request_id, str) or not request_id.strip():
        raise JuditRequestError(
            "Judit response missing request id",
            error_code="missing_request_id",
        )
    return JuditRequestResult(request_id=request_id.strip())


def _get_request_status_sync(request_id: str) -> JuditRequestStatusResult:
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id is required")
    identifier = urllib.parse.quote(request_id.strip(), safe="").replace(".", "%2E")
    body = _provider_request(
        _requests_url(f"requests/{identifier}"),
        method="GET",
        accepted_statuses={200},
    )
    status = body.get("status") if body else None
    if not isinstance(status, str) or not status.strip():
        raise JuditRequestError(
            "Judit request status response missing status",
            error_code="missing_request_status",
        )
    normalized_status = status.strip().lower()
    if not _SAFE_PROVIDER_CODE_RE.fullmatch(normalized_status):
        raise JuditRequestError(
            "Judit request status response contained unsafe status",
            error_code="invalid_request_status",
        )
    return JuditRequestStatusResult(status=normalized_status)


def _get_responses_sync(request_id: str) -> JuditResponsesResult:
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id is required")
    query = urllib.parse.urlencode({"request_id": request_id.strip()})
    body = _provider_request(
        f"{_requests_url('responses')}?{query}",
        method="GET",
        accepted_statuses={200},
    )
    page_data = body.get("page_data") if body else None
    if not isinstance(page_data, list):
        raise JuditRequestError(
            "Judit responses payload missing page_data",
            error_code="invalid_responses_payload",
        )
    request_status_raw = body.get("request_status") if body else None
    request_status = None
    if isinstance(request_status_raw, str):
        candidate = request_status_raw.strip().lower()
        if _SAFE_PROVIDER_CODE_RE.fullmatch(candidate):
            request_status = candidate

    lawsuit_count = 0
    application_info_count = 0
    application_error_count = 0
    other_response_count = 0
    direct_payload_count = 0
    application_error_code = None
    application_error_message = None
    for item in page_data:
        if not isinstance(item, dict):
            continue
        if request_status is None:
            item_status = item.get("request_status")
            if isinstance(item_status, str):
                candidate = item_status.strip().lower()
                if _SAFE_PROVIDER_CODE_RE.fullmatch(candidate):
                    request_status = candidate
        response_type = str(item.get("response_type") or "").strip().lower()
        if response_type == "lawsuit":
            lawsuit_count += 1
        elif response_type == "application_info":
            application_info_count += 1
        elif response_type == "application_error":
            application_error_count += 1
            response_data = item.get("response_data")
            if isinstance(response_data, dict):
                code = response_data.get("code")
                if isinstance(code, int) and not isinstance(code, bool):
                    application_error_code = code
                message = response_data.get("message")
                if isinstance(message, str):
                    normalized_message = message.strip()
                    if _SAFE_PROVIDER_CODE_RE.fullmatch(normalized_message):
                        application_error_message = normalized_message
        elif response_type:
            other_response_count += 1
        else:
            direct_payload_count += 1
    return JuditResponsesResult(
        request_status=request_status,
        response_count=len(page_data),
        lawsuit_response_count=lawsuit_count,
        application_info_count=application_info_count,
        application_error_count=application_error_count,
        other_response_count=other_response_count,
        direct_payload_count=direct_payload_count,
        application_error_code=application_error_code,
        application_error_message=application_error_message,
    )


def _create_tracking_sync(code: str, recurrence_days: int) -> JuditTrackingResult:
    if recurrence_days <= 0:
        raise ValueError("tracking recurrence_days must be greater than zero")
    body = _provider_request(
        JUDIT_TRACKING_URL,
        method="POST",
        payload={
            "recurrence": recurrence_days,
            "search": {
                "search_type": "lawsuit_cnj",
                "search_key": code,
                "response_type": "lawsuit",
            },
            "with_attachments": judit_attachments_enabled(),
        },
        accepted_statuses={200, 201},
    )
    tracking_id = body.get("tracking_id") if body else None
    if not isinstance(tracking_id, str) or not tracking_id.strip():
        raise JuditRequestError("Judit tracking response missing tracking id")
    status = str(body.get("status") or "created").strip().lower()
    return JuditTrackingResult(tracking_id=tracking_id.strip(), status=status)


def _delete_tracking_sync(tracking_id: str) -> None:
    if not isinstance(tracking_id, str) or not tracking_id.strip():
        raise ValueError("tracking_id is required")
    identifier = tracking_id.strip()
    encoded_identifier = urllib.parse.quote(identifier, safe="").replace(".", "%2E")
    _provider_request(
        f"{JUDIT_TRACKING_URL}/{encoded_identifier}",
        method="DELETE",
        accepted_statuses={200, 204},
        not_found_is_success=True,
    )


async def create_lawsuit_request(code: str) -> JuditRequestResult:
    return await asyncio.to_thread(_create_request_sync, code)


async def get_lawsuit_request_status(request_id: str) -> JuditRequestStatusResult:
    return await asyncio.to_thread(_get_request_status_sync, request_id)


async def get_lawsuit_responses(request_id: str) -> JuditResponsesResult:
    return await asyncio.to_thread(_get_responses_sync, request_id)


async def create_lawsuit_tracking(code: str, *, recurrence_days: int = 1) -> JuditTrackingResult:
    return await asyncio.to_thread(_create_tracking_sync, code, recurrence_days)


async def delete_lawsuit_tracking(tracking_id: str) -> None:
    await asyncio.to_thread(_delete_tracking_sync, tracking_id)


async def download_lawsuit_attachment(
    code: str,
    *,
    instance: str | int,
    attachment_id: str,
) -> JuditAttachmentDownload:
    """Download already-authorized Judit attachment bytes behind the provider boundary."""
    return await asyncio.to_thread(
        _download_attachment_sync,
        code,
        instance,
        attachment_id,
    )
