from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def parse_browser_origins(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []

    origins: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        origin = value.strip()
        if not origin:
            continue
        if "*" in origin:
            raise ValueError("RPY_BROWSER_ORIGINS must not contain wildcards")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port not in {None, 443}
        ):
            raise ValueError("RPY_BROWSER_ORIGINS entries must be bare HTTPS origins")
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ValueError("RPY_BROWSER_ORIGINS must use public DNS hostnames")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("RPY_BROWSER_ORIGINS must use public DNS hostnames")
        canonical = f"https://{hostname}"
        if canonical in seen:
            raise ValueError("RPY_BROWSER_ORIGINS must not contain duplicates")
        seen.add(canonical)
        origins.append(canonical)
    return origins
