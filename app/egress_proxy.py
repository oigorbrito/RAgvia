from __future__ import annotations

import asyncio
import hmac
import ipaddress
import math
import os
import re
import socket
from collections.abc import Iterable

DEFAULT_PORT = 3128
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
MAX_HEADER_BYTES = 8192
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")


class UnsafeEgressResolutionError(RuntimeError):
    pass


def _public_endpoints_from_addrinfo(
    records: Iterable[tuple[int, int, int, str, tuple]],
) -> tuple[tuple[int, str], ...]:
    endpoints: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in records:
        if family not in {socket.AF_INET, socket.AF_INET6} or not sockaddr:
            raise UnsafeEgressResolutionError("egress DNS returned an unsupported address")
        address = str(sockaddr[0])
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeEgressResolutionError(
                "egress DNS returned an invalid address"
            ) from exc
        if not parsed.is_global:
            raise UnsafeEgressResolutionError(
                "egress DNS resolved an allowlisted hostname to a non-global address"
            )
        endpoint = (family, parsed.compressed)
        if endpoint not in seen:
            seen.add(endpoint)
            endpoints.append(endpoint)
    if not endpoints:
        raise OSError("egress DNS returned no addresses")
    return tuple(endpoints)


async def _resolve_public_endpoints(host: str) -> tuple[tuple[int, str], ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host,
        443,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return _public_endpoints_from_addrinfo(records)


async def _open_public_connection(
    host: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    endpoints = await _resolve_public_endpoints(host)
    last_error: OSError | None = None
    for family, address in endpoints:
        try:
            return await asyncio.open_connection(
                address,
                443,
                family=family,
                flags=socket.AI_NUMERICHOST,
            )
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("no public egress endpoint could be reached")


def _canonical_dns_hostname(value: str) -> str:
    host = value.strip().rstrip(".").casefold()
    if not host:
        raise ValueError("empty hostname")
    try:
        canonical = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid hostname") from exc
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        pass
    else:
        raise ValueError("IP literals are not allowed")
    if not _HOST_RE.fullmatch(canonical) or ".." in canonical:
        raise ValueError("invalid hostname")
    return canonical


def _canonical_allowed_host(value: str) -> str:
    try:
        return _canonical_dns_hostname(value)
    except ValueError as exc:
        if str(exc) == "empty hostname":
            raise RuntimeError("egress proxy allowlist contains an empty hostname") from exc
        if str(exc) == "IP literals are not allowed":
            raise RuntimeError(
                "egress proxy allowlist must contain DNS hostnames, not IP addresses"
            ) from exc
        raise RuntimeError("egress proxy allowlist contains an invalid hostname") from exc


def _normalize_allowed_hosts(values: Iterable[str]) -> frozenset[str]:
    hosts = frozenset(_canonical_allowed_host(str(item)) for item in values)
    if not hosts:
        raise RuntimeError("egress proxy allowlist must not be empty")
    return hosts


def _allowed_hosts(raw: str | None = None) -> frozenset[str]:
    value = os.environ.get("EGRESS_PROXY_ALLOWED_HOSTS", "") if raw is None else raw
    items = [item for item in value.split(",") if item.strip()]
    if not items:
        raise RuntimeError("EGRESS_PROXY_ALLOWED_HOSTS must contain at least one hostname")
    try:
        return _normalize_allowed_hosts(items)
    except RuntimeError as exc:
        message = str(exc).replace("egress proxy allowlist", "EGRESS_PROXY_ALLOWED_HOSTS")
        raise RuntimeError(message) from exc


def _connect_timeout() -> float:
    raw = os.environ.get("EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS", str(DEFAULT_CONNECT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must be numeric") from exc
    if not math.isfinite(value) or not 0 < value <= 60:
        raise RuntimeError(
            "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must be a finite number between 0 and 60"
        )
    return value


def _parse_connect_target(target: str) -> str:
    if target.count(":") != 1:
        raise ValueError("CONNECT target must be hostname:port")
    raw_host, raw_port = target.rsplit(":", 1)
    try:
        host = _canonical_dns_hostname(raw_host)
    except ValueError as exc:
        if str(exc) == "IP literals are not allowed":
            raise ValueError("CONNECT IP literals are not allowed") from exc
        raise ValueError("invalid CONNECT hostname") from exc
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("invalid CONNECT port") from exc
    if port != 443:
        raise ValueError("only TLS port 443 is allowed")
    return host


async def _write_response(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(
        (
            f"HTTP/1.1 {status}\r\n"
            "Connection: close\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str]:
    total = 0
    request_line = await reader.readline()
    total += len(request_line)
    if not request_line or total > MAX_HEADER_BYTES:
        raise ValueError("invalid proxy request")
    try:
        line = request_line.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("proxy request line must be ASCII") from exc
    parts = line.split()
    if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ValueError("invalid proxy request line")
    method, target, _ = parts

    while True:
        header = await reader.readline()
        total += len(header)
        if total > MAX_HEADER_BYTES:
            raise ValueError("proxy request headers exceed safe size")
        if header in {b"\r\n", b"\n", b""}:
            break
    return method.upper(), target


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.write_eof()
        except (AttributeError, OSError, RuntimeError):
            pass


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    allowed_hosts: frozenset[str],
    timeout_seconds: float,
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        method, target = await asyncio.wait_for(
            _read_request(reader),
            timeout=timeout_seconds,
        )
        if method != "CONNECT":
            await _write_response(writer, "405 Method Not Allowed")
            return
        requested_host = _parse_connect_target(target)
        authorized_host = next(
            (
                configured_host
                for configured_host in allowed_hosts
                if hmac.compare_digest(requested_host, configured_host)
            ),
            None,
        )
        if authorized_host is None:
            await _write_response(writer, "403 Forbidden")
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                _open_public_connection(authorized_host),
                timeout=timeout_seconds,
            )
        except UnsafeEgressResolutionError:
            await _write_response(writer, "403 Forbidden")
            return
        except (OSError, TimeoutError):
            await _write_response(writer, "502 Bad Gateway")
            return

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            _relay(reader, upstream_writer),
            _relay(upstream_reader, writer),
        )
    except (ValueError, asyncio.LimitOverrunError, TimeoutError):
        try:
            await _write_response(writer, "400 Bad Request")
        except ConnectionError:
            pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except ConnectionError:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def serve(
    *,
    host: str | None = None,
    port: int = DEFAULT_PORT,
    allowed_hosts: Iterable[str] | None = None,
) -> None:
    bind_host = str(
        host if host is not None else os.environ.get("EGRESS_PROXY_BIND_HOST", "")
    ).strip()
    if not bind_host:
        raise RuntimeError("EGRESS_PROXY_BIND_HOST is required")

    configured = (
        _normalize_allowed_hosts(allowed_hosts)
        if allowed_hosts is not None
        else _allowed_hosts()
    )
    timeout_seconds = _connect_timeout()
    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(
            reader,
            writer,
            allowed_hosts=configured,
            timeout_seconds=timeout_seconds,
        ),
        bind_host,
        port,
        limit=MAX_HEADER_BYTES,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
