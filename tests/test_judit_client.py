from __future__ import annotations

import io
import json
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest

from app import judit_client


class _Headers:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    def get(self, name: str):
        return self.values.get(name)


class _Response:
    status = 201

    def __init__(
        self,
        body: bytes,
        *,
        status: int = 201,
        headers: dict[str, str] | None = None,
    ):
        self.body = body
        self.status = status
        self.headers = _Headers(headers)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit: int):
        return self.body


def test_create_request_uses_cnj_contract_without_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "secret-provider-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"request_id":"req-123"}'),
    )
    result = judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    request = captured["request"]
    assert result.request_id == "req-123"
    assert request.full_url == judit_client.JUDIT_REQUESTS_URL
    assert request.method == "POST"
    assert request.get_header("Api-key") == "secret-provider-key"
    assert json.loads(request.data) == {
        "search": {
            "search_type": "lawsuit_cnj",
            "search_key": "0000000-00.0000.0.00.0001",
        },
        "with_attachments": False,
    }


def test_download_attachment_uses_authenticated_bounded_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "attachment-key")
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "16")
    monkeypatch.setattr(
        judit_client,
        "_open_attachment_response",
        lambda request, *, timeout: captured.update(request=request, timeout=timeout)
        or _Response(
            b"%PDF-synthetic",
            status=200,
            headers={"Content-Type": "application/pdf; charset=binary"},
        ),
    )

    result = judit_client._download_attachment_sync(
        "0000000-00.0000.0.00.0001",
        1,
        "att/id 123",
    )

    request = captured["request"]
    assert request.full_url == (
        f"{judit_client.JUDIT_LAWSUITS_URL}/"
        "0000000-00.0000.0.00.0001/1/attachments/att%2Fid%20123"
    )
    assert request.method == "GET"
    assert request.get_header("Api-key") == "attachment-key"
    assert result.content_type == "application/pdf"
    assert result.data == b"%PDF-synthetic"


def test_download_attachment_rejects_oversized_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "attachment-key")
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "4")
    monkeypatch.setattr(
        judit_client,
        "_open_attachment_response",
        lambda *_args, **_kwargs: _Response(b"12345", status=200),
    )

    with pytest.raises(judit_client.JuditRequestError, match="exceeded safe size"):
        judit_client._download_attachment_sync(
            "0000000-00.0000.0.00.0001",
            1,
            "att-1",
        )


def test_download_attachment_http_error_discards_body_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "super-secret-attachment-key")

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "forbidden sensitive provider body",
            {},
            None,
        )

    monkeypatch.setattr(judit_client, "_open_attachment_response", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._download_attachment_sync(
            "0000000-00.0000.0.00.0001",
            1,
            "att-1",
        )

    assert "403" in str(exc.value)
    assert "super-secret-attachment-key" not in str(exc.value)
    assert "sensitive provider body" not in str(exc.value)
    assert exc.value.retry_safe is True


def test_download_attachment_does_not_forward_api_key_across_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redirected_requests: list[dict[str, str | None]] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{sink.server_port}/capture",
            )
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            redirected_requests.append({"path": self.path, "api_key": self.headers.get("api-key")})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"unexpected")

        def log_message(self, *_args) -> None:
            pass

    sink = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    redirector = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (sink, redirector)]
    for thread in threads:
        thread.start()
    monkeypatch.setenv("JUDIT_API_KEY", "synthetic-attachment-secret")
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "128")
    monkeypatch.setattr(
        judit_client,
        "JUDIT_LAWSUITS_URL",
        f"http://127.0.0.1:{redirector.server_port}",
    )
    try:
        with pytest.raises(judit_client.JuditRequestError, match="HTTP 302"):
            judit_client._download_attachment_sync("synthetic-process", 1, "synthetic-id")
        assert redirected_requests == []
    finally:
        redirector.shutdown()
        sink.shutdown()
        redirector.server_close()
        sink.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_download_attachment_requires_safe_identifiers() -> None:
    with pytest.raises(ValueError, match="process code"):
        judit_client._download_attachment_sync("", 1, "att-1")
    with pytest.raises(ValueError, match="instance"):
        judit_client._download_attachment_sync("0000000-00.0000.0.00.0001", "", "att-1")
    with pytest.raises(ValueError, match="attachment_id"):
        judit_client._download_attachment_sync("0000000-00.0000.0.00.0001", 1, "")


def test_create_tracking_uses_lawsuit_cnj_and_recurrence(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "tracking-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"tracking_id":"track-123","status":"created"}', status=201),
    )

    result = judit_client._create_tracking_sync("0000000-00.0000.0.00.0001", 2)

    request = captured["request"]
    assert result.tracking_id == "track-123"
    assert result.status == "created"
    assert request.full_url == judit_client.JUDIT_TRACKING_URL
    assert request.method == "POST"
    assert json.loads(request.data) == {
        "recurrence": 2,
        "search": {
            "search_type": "lawsuit_cnj",
            "search_key": "0000000-00.0000.0.00.0001",
            "response_type": "lawsuit",
        },
        "with_attachments": False,
    }


def test_delete_tracking_is_idempotent_on_provider_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "tracking-key")

    def missing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", missing)
    judit_client._delete_tracking_sync("track-123")


def test_provider_error_discards_body_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "secret-provider-key")

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(judit_client.JUDIT_REQUESTS_URL, 401, "bad", {}, None)

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)
    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    assert "401" in str(exc.value)
    assert "secret-provider-key" not in str(exc.value)
    assert exc.value.retry_safe is True


@pytest.mark.parametrize("status", [408, 429, 500, 502])
def test_ambiguous_judit_http_failures_are_not_retry_safe(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(judit_client.JUDIT_REQUESTS_URL, status, "failed", {}, None)

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)
    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    assert exc.value.retry_safe is False


def test_transport_failure_is_not_retry_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    assert exc.value.retry_safe is False


def test_missing_request_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"{}"),
    )
    with pytest.raises(judit_client.JuditRequestError, match="missing request id"):
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")


def test_missing_tracking_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"{}", status=201),
    )
    with pytest.raises(judit_client.JuditRequestError, match="missing tracking id"):
        judit_client._create_tracking_sync("0000000-00.0000.0.00.0001", 1)


def test_tracking_recurrence_must_be_positive() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        judit_client._create_tracking_sync("0000000-00.0000.0.00.0001", 0)


def test_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_TIMEOUT_SECONDS", "61")
    with pytest.raises(RuntimeError, match="finite number between 0 and 60"):
        judit_client._timeout_seconds()


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf"])
def test_timeout_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("JUDIT_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="finite number between 0 and 60"):
        judit_client._timeout_seconds()


def test_attachment_max_bytes_is_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        judit_client._attachment_max_bytes()



def test_attachments_require_explicit_enabled_direct_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.delenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", raising=False)
    with pytest.raises(RuntimeError, match="direct_api_key"):
        judit_client.judit_attachments_enabled()

    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")
    assert judit_client.judit_attachments_enabled() is True


def test_create_request_enables_attachments_only_after_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "attachment-key")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"request_id":"req-att"}'),
    )

    result = judit_client._create_request_sync("0000000-00.0000.0.00.0001")

    assert result.request_id == "req-att"
    assert json.loads(captured["request"].data)["with_attachments"] is True


def test_judit_response_rejects_duplicate_json_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", uuid4().hex)
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            b'{"request_id":"first","request_id":"second"}'
        ),
    )
    with pytest.raises(judit_client.JuditRequestError, match="invalid response"):
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_judit_response_rejects_non_standard_numeric_constants(
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", uuid4().hex)
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            f'{{"request_id":"req-1","score":{constant}}}'.encode("utf-8")
        ),
    )
    with pytest.raises(judit_client.JuditRequestError, match="invalid response"):
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")


def test_delete_tracking_percent_encodes_provider_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "tracking-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b"", status=204),
    )

    judit_client._delete_tracking_sync("track/id ?#")

    request = captured["request"]
    expected = f"{judit_client.JUDIT_TRACKING_URL}/track%2Fid%20%3F%23"
    if request.full_url != expected:
        raise AssertionError(f"unexpected tracking delete URL: {request.full_url!r}")


@pytest.mark.parametrize(
    ("tracking_id", "expected_suffix"),
    [
        ("..", "%2E%2E"),
        (".", "%2E"),
        (" track.id ", "track%2Eid"),
    ],
)
def test_delete_tracking_encodes_dot_segments_and_trims_whitespace(
    monkeypatch: pytest.MonkeyPatch,
    tracking_id: str,
    expected_suffix: str,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "tracking-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b"", status=204),
    )

    judit_client._delete_tracking_sync(tracking_id)

    request = captured["request"]
    expected = f"{judit_client.JUDIT_TRACKING_URL}/{expected_suffix}"
    if request.full_url != expected:
        raise AssertionError(f"unexpected tracking delete URL: {request.full_url!r}")


@pytest.mark.parametrize("tracking_id", ["", " ", "\t\n", None, b"track", 123])
def test_delete_tracking_rejects_empty_or_non_string_identifier(
    tracking_id,
) -> None:
    with pytest.raises(ValueError, match="tracking_id is required"):
        judit_client._delete_tracking_sync(tracking_id)


def test_create_request_includes_explicit_https_callback_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "callback-key")
    monkeypatch.setenv(
        "JUDIT_CALLBACK_URL",
        "https://staging.example.test/webhooks/judit/token-value",
    )
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"request_id":"req-callback"}'),
    )

    result = judit_client._create_request_sync("0000000-00.0000.0.00.0001")

    assert result.request_id == "req-callback"
    assert json.loads(captured["request"].data)["callback_url"] == (
        "https://staging.example.test/webhooks/judit/token-value"
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://staging.example.test/webhooks/judit/token",
        "https://user:pass@staging.example.test/webhooks/judit/token",
        "https://staging.example.test/webhooks/judit/token?secret=query",
        "https://staging.example.test/webhooks/judit/token#fragment",
        "/webhooks/judit/token",
    ],
)
def test_callback_url_rejects_unsafe_or_non_public_shape(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "callback-key")
    monkeypatch.setenv("JUDIT_CALLBACK_URL", value)
    with pytest.raises(RuntimeError, match="JUDIT_CALLBACK_URL"):
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")


def test_create_request_omits_callback_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "callback-key")
    monkeypatch.delenv("JUDIT_CALLBACK_URL", raising=False)
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"request_id":"req-no-callback"}'),
    )

    judit_client._create_request_sync("0000000-00.0000.0.00.0001")

    assert "callback_url" not in json.loads(captured["request"].data)


def test_connectivity_check_uses_non_creating_get(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"page_data":[]}', status=200),
    )

    body = judit_client._check_connectivity_sync()

    request = captured["request"]
    assert request.method == "GET"
    assert request.data is None
    assert request.full_url.endswith("/requests?page=1&page_size=1")
    assert request.get_header("Api-key") == "diagnostic-key"
    assert body == {"page_data": []}


def test_requests_host_can_use_known_compat_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")
    monkeypatch.setenv("JUDIT_REQUESTS_BASE_URL", judit_client.JUDIT_REQUESTS_COMPAT_BASE_URL)
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"page_data":[]}', status=200),
    )

    judit_client._check_connectivity_sync()

    assert captured["request"].full_url == (
        "https://requests.production.judit.io/requests?page=1&page_size=1"
    )


def test_requests_host_rejects_unapproved_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_REQUESTS_BASE_URL", "https://example.invalid")

    with pytest.raises(RuntimeError, match="approved Judit requests host"):
        judit_client._requests_base_url()


@pytest.mark.parametrize(
    ("status", "error_code"),
    [(401, "http_401"), (403, "http_403"), (429, "http_429"), (500, "http_500")],
)
def test_connectivity_diagnostic_exposes_only_safe_http_classification(
    monkeypatch: pytest.MonkeyPatch, status: int, error_code: str
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            judit_client.JUDIT_REQUESTS_URL,
            status,
            "provider secret body",
            {},
            None,
        )

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._check_connectivity_sync()

    assert exc.value.error_code == error_code
    assert exc.value.http_status == status
    assert "provider secret body" not in str(exc.value)


def test_connectivity_diagnostic_extracts_only_allowlisted_provider_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "provider validation failed",
            {},
            io.BytesIO(
                b'{"error":{"name":"HttpBadRequestError","data":"INVALID_QUERY"}}'
            ),
        )

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._check_connectivity_sync()

    assert exc.value.error_code == "http_400"
    assert exc.value.provider_error_code == "HttpBadRequestError"
    assert "provider validation failed" not in str(exc.value)


def test_connectivity_diagnostic_rejects_unsafe_provider_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "provider validation failed",
            {},
            io.BytesIO(
                b'{"error":{"data":"contains spaces and possibly sensitive text"}}'
            ),
        )

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._check_connectivity_sync()

    assert exc.value.provider_error_code is None


def test_connectivity_diagnostic_extracts_sanitized_validation_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "provider validation failed",
            {},
            io.BytesIO(
                b'{"error":{"name":"HttpBadRequestError","message":"BAD_REQUEST","data":['
                b'{"field":"page","rule":"required","message":"page is required"},'
                b'{"field":"page_size","rule":"min","message":"too small"}]}}'
            ),
        )

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._check_connectivity_sync()

    assert exc.value.provider_error_code == "HttpBadRequestError"
    assert exc.value.provider_validation == [
        {"field": "page", "rule": "required"},
        {"field": "page_size", "rule": "min"},
    ]
    assert "page is required" not in str(exc.value)


def test_connectivity_diagnostic_drops_unsafe_validation_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "diagnostic-key")

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "provider validation failed",
            {},
            io.BytesIO(
                b'{"error":{"name":"HttpBadRequestError","data":['
                b'{"field":"bad field with spaces","rule":"bad rule with spaces",'
                b'"message":"possibly sensitive free text"}]}}'
            ),
        )

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._check_connectivity_sync()

    assert exc.value.provider_validation == []
