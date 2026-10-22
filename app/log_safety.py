from __future__ import annotations

import json
import os
import re

REDACTED = "[REDACTED]"
MAX_ERROR_MESSAGE_CHARS = 2000

_SECRET_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "JUDIT_API_KEY",
    "DATAJUD_API_KEY",
    "COHERE_API_KEY",
    "LANGFUSE_SECRET_KEY",
    "JUDIT_WEBHOOK_TOKEN",
    "JUDIT_CALLBACK_URL",
    "RPY_BEARER_TOKENS",
    "RPY_DEMO_BEARER_TOKEN",
    "RPY_OPS_TOKEN",
    "POSTGRES_PASSWORD",
    "PGPASSWORD",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "BACKUP_DATABASE_URL",
)

# Redact credentials embedded in URLs even when the full URL is not available in
# environment variables (for example when emitted by a lower-level client).
_URI_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^\s/:@]*):(?P<secret>[^\s/@]+)@")
_QUOTED_AUTHORIZATION_RE = re.compile(
    r"""(?ix)
    (?P<prefix>\bauthorization[\"']?\s*[:=]\s*)
    (?P<quote>[\"'])
    (?P<scheme>bearer|apikey|basic|token|digest|negotiate|oauth)
    (?P<spacing>\s+)(?=\S)
    (?:\\.|(?! (?P=quote) )[^\r\n}])+
    (?P<closing_quote>(?P=quote)|(?=[}\]]|$))
    """
)
_DIGEST_AUTHORIZATION_RE = re.compile(
    r"""(?imx)
    (\bauthorization[\"']?\s*[:=]\s*[\"']?\s*digest\s+)
    (?=(?:username|realm|nonce|uri|response|algorithm|cnonce|opaque|qop|nc)\s*=)
    [^\r\n]+
    """
)
_AUTHORIZATION_RE = re.compile(
    r"""(?ix)
    (\bauthorization[\"']?\s*[:=]\s*[\"']?\s*
     (?:bearer|apikey|basic|token|digest|negotiate|oauth)\s+)
    (\[REDACTED\][^\s,;}\"']*|[^\s,;}\"'\]]+)
    """
)
_STRUCTURED_SECRET_RE = re.compile(
    r"""(?ix)
    (?P<prefix>["']?(?:api[_-]?key|secret[_-]?key|private[_-]?key|access[_-]?key|app[_-]?key|auth[_-]?key|signing[_-]?key|encryption[_-]?key|client[_-]?secret|app[_-]?secret|auth[_-]?token|refresh[_-]?token|bearer[_-]?token|session[_-]?token|id[_-]?token|user[_-]?token|api[_-]?token|access[_-]?token|webhook[_-]?(?:token|key|secret)|callback[_-]?(?:token|key|secret)|bearer|token|password|passcode|passwd|pwd|credential|credentials|secret)["']?\s*[=:]\s*)
    (?P<quote>["'])?
    (?P<secret>
        (?(quote)
            (?: \\. | (?! (?P=quote) ) [^\\\r\n] )+
            |
            (?: \[REDACTED\] [^"'\s,;&}\]]* | [^"'\s,;&}\]]+ )
        )
    )
    (?(quote)(?P=quote))
    """
)
# Redact application/provider API keys even when not configured in env or when
# appearing without authorization headers or query parameter names.
_API_KEY_TOKEN_RE = re.compile(
    r"\b(?:sk_(?:live|test)_[a-zA-Z0-9_-]+|sk-[a-zA-Z0-9_-]{12,}|rpy_[a-zA-Z0-9_-]{12,}|eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\b"
)


def _configured_bearer_token_keys(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    return [
        token
        for token in parsed
        if isinstance(token, str) and len(token) >= 4
    ]


def _configured_secret_values() -> list[str]:
    values: list[str] = []
    for name in _SECRET_ENV_NAMES:
        value = os.environ.get(name)
        if not value or len(value) < 4:
            continue
        values.append(value)
        if name == "RPY_BEARER_TOKENS":
            values.extend(_configured_bearer_token_keys(value))
    return sorted(set(values), key=len, reverse=True)


def sanitize_error_message(value: object, *, max_chars: int = MAX_ERROR_MESSAGE_CHARS) -> str:
    """Return a bounded error string safe for logs and durable job error storage."""
    text = str(value)

    for secret in _configured_secret_values():
        text = text.replace(secret, REDACTED)

    text = _URI_CREDENTIALS_RE.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@",
        text,
    )
    text = _QUOTED_AUTHORIZATION_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"{match.group('scheme')}{match.group('spacing')}"
            f"{REDACTED}{match.group('closing_quote')}"
        ),
        text,
    )
    text = _DIGEST_AUTHORIZATION_RE.sub(
        lambda match: f"{match.group(1)}{REDACTED}", text
    )
    text = _AUTHORIZATION_RE.sub(
        lambda match: f"{match.group(1)}{REDACTED}", text
    )
    text = _STRUCTURED_SECRET_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote') or ''}"
            f"{REDACTED}{match.group('quote') or ''}"
        ),
        text,
    )
    text = _API_KEY_TOKEN_RE.sub(REDACTED, text)

    if max_chars <= 0:
        return ""
    if len(text) > max_chars:
        suffix = "… [truncated]"
        if max_chars <= len(suffix):
            return text[:max_chars]
        text = text[: max_chars - len(suffix)] + suffix
    return text
