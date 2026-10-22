from __future__ import annotations

import asyncio
import socket

import pytest
from hypothesis import given, settings, strategies as st

from app import egress_proxy


def test_allowlist_requires_dns_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "EGRESS_PROXY_ALLOWED_HOSTS",
        "api.anthropic.com, requests.production.judit.io",
    )
    assert egress_proxy._allowed_hosts() == frozenset(  # nosec B101
        {"api.anthropic.com", "requests.production.judit.io"}
    )

    monkeypatch.setenv("EGRESS_PROXY_ALLOWED_HOSTS", "127.0.0.1")
    with pytest.raises(RuntimeError, match="DNS hostnames"):
        egress_proxy._allowed_hosts()


def test_programmatic_allowlist_uses_same_validation_as_environment() -> None:
    assert egress_proxy._normalize_allowed_hosts(
        ["API.ANTHROPIC.COM.", "requests.production.judit.io"]
    ) == frozenset({"api.anthropic.com", "requests.production.judit.io"})  # nosec B101

    with pytest.raises(RuntimeError, match="DNS hostnames"):
        egress_proxy._normalize_allowed_hosts(["127.0.0.1"])


@settings(max_examples=96, deadline=None)
@given(address=st.ip_addresses())
def test_programmatic_allowlist_property_rejects_ip_literals(address) -> None:
    with pytest.raises(RuntimeError, match="DNS hostnames"):
        egress_proxy._normalize_allowed_hosts([str(address)])


@settings(max_examples=96, deadline=None)
@given(address=st.ip_addresses())
def test_connect_target_property_rejects_ip_literal_destinations(address) -> None:
    target = f"{address}:443" if address.version == 4 else f"[{address}]:443"
    with pytest.raises(ValueError):
        egress_proxy._parse_connect_target(target)


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf"])
def test_egress_connect_timeout_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="finite number between 0 and 60"):
        egress_proxy._connect_timeout()


@settings(max_examples=96, deadline=None)
@given(address=st.ip_addresses().filter(lambda value: not value.is_global))
def test_resolved_endpoints_property_rejects_non_global_addresses(address) -> None:
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    sockaddr = (str(address), 443) if address.version == 4 else (str(address), 443, 0, 0)
    with pytest.raises(egress_proxy.UnsafeEgressResolutionError, match="non-global"):
        egress_proxy._public_endpoints_from_addrinfo(
            [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
        )


def test_resolved_endpoints_accept_public_addresses_and_deduplicate() -> None:
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
        (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("2606:4700:4700::1111", 443, 0, 0),
        ),
    ]
    assert egress_proxy._public_endpoints_from_addrinfo(records) == (  # nosec B101
        (socket.AF_INET, "8.8.8.8"),
        (socket.AF_INET6, "2606:4700:4700::1111"),
    )


@pytest.mark.asyncio
async def test_public_connection_uses_resolved_numeric_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, int, int, int]] = []

    async def fake_resolve(host: str):
        assert host == "api.anthropic.com"  # nosec B101
        return ((socket.AF_INET, "8.8.8.8"),)

    async def fake_connect(host: str, port: int, *, family: int, flags: int):
        observed.append((host, port, family, flags))
        return asyncio.StreamReader(), _Writer()  # type: ignore[return-value]

    monkeypatch.setattr(egress_proxy, "_resolve_public_endpoints", fake_resolve)
    monkeypatch.setattr(asyncio, "open_connection", fake_connect)

    await egress_proxy._open_public_connection("api.anthropic.com")

    assert observed == [  # nosec B101
        ("8.8.8.8", 443, socket.AF_INET, socket.AI_NUMERICHOST)
    ]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("api.anthropic.com:443", "api.anthropic.com"),
        ("API.ANTHROPIC.COM.:443", "api.anthropic.com"),
    ],
)
def test_connect_target_is_exact_tls_hostname(target: str, expected: str) -> None:
    assert egress_proxy._parse_connect_target(target) == expected  # nosec B101


@pytest.mark.parametrize(
    "target",
    [
        "api.anthropic.com:80",
        "127.0.0.1:443",
        "[::1]:443",
        "api.anthropic.com:443:extra",
        "bad host:443",
    ],
)
def test_connect_target_rejects_bypass_shapes(target: str) -> None:
    with pytest.raises(ValueError):
        egress_proxy._parse_connect_target(target)


class _Writer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_disallowed_connect_is_rejected_before_dns_or_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = (
        b"CONNECT metadata.google.internal:443 HTTP/1.1\r\n"
        b"Host: metadata.google.internal:443\r\n\r\n"
    )
    reader = asyncio.StreamReader()
    reader.feed_data(request)
    reader.feed_eof()
    writer = _Writer()

    async def must_not_connect(*_args, **_kwargs):
        raise AssertionError("disallowed destination must not reach network")

    monkeypatch.setattr(asyncio, "open_connection", must_not_connect)
    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"api.anthropic.com"}),
        timeout_seconds=1.0,
    )

    assert b"403 Forbidden" in bytes(writer.data)  # nosec B101
    assert writer.closed is True  # nosec B101


@pytest.mark.asyncio
async def test_plain_http_proxying_is_not_supported() -> None:
    request = b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n"
    reader = asyncio.StreamReader()
    reader.feed_data(request)
    reader.feed_eof()
    writer = _Writer()

    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"example.com"}),
        timeout_seconds=1.0,
    )

    assert b"405 Method Not Allowed" in bytes(writer.data)  # nosec B101



@pytest.mark.asyncio
async def test_idle_handshake_times_out_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = asyncio.StreamReader()
    writer = _Writer()

    async def stall_read_request(_reader):
        await asyncio.sleep(10)
        raise AssertionError("handshake timeout should cancel stalled parsing")

    monkeypatch.setattr(egress_proxy, "_read_request", stall_read_request)
    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"api.anthropic.com"}),
        timeout_seconds=0.01,
    )

    assert b"400 Bad Request" in bytes(writer.data)  # nosec B101
    assert writer.closed is True  # nosec B101



@pytest.mark.asyncio
async def test_upstream_socket_uses_authorized_allowlist_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = (
        b"CONNECT API.ANTHROPIC.COM.:443 HTTP/1.1\r\n"
        b"Host: API.ANTHROPIC.COM.:443\r\n\r\n"
    )
    reader = asyncio.StreamReader()
    reader.feed_data(request)
    reader.feed_eof()
    writer = _Writer()
    observed: list[str] = []

    class _UpstreamWriter(_Writer):
        def write_eof(self) -> None:
            return None

    upstream_reader = asyncio.StreamReader()
    upstream_reader.feed_eof()
    upstream_writer = _UpstreamWriter()

    async def fake_connect(host: str):
        observed.append(host)
        return upstream_reader, upstream_writer

    monkeypatch.setattr(egress_proxy, "_open_public_connection", fake_connect)
    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"api.anthropic.com"}),
        timeout_seconds=1.0,
    )

    assert observed == ["api.anthropic.com"]  # nosec B101
    assert b"200 Connection Established" in bytes(writer.data)  # nosec B101


@pytest.mark.asyncio
async def test_allowlisted_hostname_resolving_private_is_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = (
        b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n"
        b"Host: api.anthropic.com:443\r\n\r\n"
    )
    reader = asyncio.StreamReader()
    reader.feed_data(request)
    reader.feed_eof()
    writer = _Writer()

    async def unsafe_resolution(host: str):
        raise egress_proxy.UnsafeEgressResolutionError("non-global")

    monkeypatch.setattr(egress_proxy, "_open_public_connection", unsafe_resolution)
    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"api.anthropic.com"}),
        timeout_seconds=1.0,
    )

    assert b"403 Forbidden" in bytes(writer.data)  # nosec B101
    assert writer.closed is True  # nosec B101
