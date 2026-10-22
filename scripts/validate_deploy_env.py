from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from typing import Callable

from app.browser_origin_config import parse_browser_origins
from urllib.parse import urlsplit
from uuid import UUID

DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
DB_URL_KEYS = (
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "BACKUP_DATABASE_URL",
)
REQUIRED_KEYS = (
    "RPY_IMAGE",
    "POSTGRES_PASSWORD",
    *DB_URL_KEYS,
    "ANTHROPIC_API_KEY",
    "JUDIT_API_KEY",
    "JUDIT_CALLBACK_URL",
    "JUDIT_WEBHOOK_TOKEN",
    "RPY_BEARER_TOKENS",
    "RPY_OPS_TOKEN",
    "EGRESS_PROXY_ALLOWED_HOSTS",
)
PLACEHOLDER_MARKERS = ("replace-with", "<64-hex-digest>", "example")
BGE_MODEL = "BAAI/bge-m3"
COHERE_MODEL = "embed-v4.0"
BGE_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
COHERE_RERANKER_MODEL = "rerank-v4.0-pro"
LANGFUSE_ENVIRONMENT_RE = re.compile(r"^(?!langfuse)[a-z0-9_-]{1,40}$")
EGRESS_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
_BASE_EGRESS_HOSTS = {
    "api.anthropic.com",
    "requests.prod.judit.io",
    "tracking.production.judit.io",
}


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate JSON object key")
        parsed[key] = value
    return parsed


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment line: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("environment variable name must not be empty")
        if key in values:
            raise ValueError(f"duplicate environment variable: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values



def _url_hostname(value: str, key: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{key} must be an HTTPS URL with a hostname")
    return parsed.hostname.rstrip(".").casefold()


def _parse_egress_hosts(value: str) -> set[str]:
    hosts: set[str] = set()
    for raw in value.split(","):
        host = raw.strip().rstrip(".").casefold()
        if not host:
            continue
        try:
            canonical = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("EGRESS_PROXY_ALLOWED_HOSTS contains an invalid hostname") from exc
        if not EGRESS_HOST_RE.fullmatch(canonical) or ".." in canonical or "*" in canonical:
            raise ValueError("EGRESS_PROXY_ALLOWED_HOSTS contains an invalid hostname")
        try:
            ipaddress.ip_address(canonical)
        except ValueError:
            pass
        else:
            raise ValueError("EGRESS_PROXY_ALLOWED_HOSTS must contain DNS hostnames, not IP addresses")
        hosts.add(canonical)
    if not hosts:
        raise ValueError("EGRESS_PROXY_ALLOWED_HOSTS must contain at least one hostname")
    return hosts


def _required_egress_hosts(values: dict[str, str]) -> set[str]:
    required = set(_BASE_EGRESS_HOSTS)

    runtime_enabled = _bool_value(values, "EMBEDDING_SPACE_RUNTIME_ENABLED", False)
    if not runtime_enabled:
        required.add("api.openai.com")
    elif str(values.get("EMBEDDING_PROVIDER") or "bge").strip().casefold() == "cohere":
        required.add("api.cohere.com")

    if (
        _bool_value(values, "RERANKER_ENABLED", False)
        and str(values.get("RERANKER_PROVIDER") or "bge").strip().casefold() == "cohere"
    ):
        required.add("api.cohere.com")

    if _bool_value(values, "JUDIT_ATTACHMENTS_ENABLED", False):
        required.add("lawsuits.production.judit.io")

    if _bool_value(values, "DATAJUD_ENABLED", False):
        required.add(
            _url_hostname(
                str(values.get("DATAJUD_BASE_URL") or "https://api-publica.datajud.cnj.jus.br"),
                "DATAJUD_BASE_URL",
            )
        )

    if _bool_value(values, "LANGFUSE_ENABLED", False):
        required.add(_url_hostname(str(values.get("LANGFUSE_BASE_URL") or ""), "LANGFUSE_BASE_URL"))

    return required


def _validate_egress_allowlist(values: dict[str, str], errors: list[str]) -> None:
    raw = str(values.get("EGRESS_PROXY_ALLOWED_HOSTS") or "").strip()
    if not raw:
        return
    try:
        allowed = _parse_egress_hosts(raw)
        required = _required_egress_hosts(values)
    except ValueError as exc:
        errors.append(str(exc))
        return
    missing = sorted(required - allowed)
    if missing:
        errors.append(
            "EGRESS_PROXY_ALLOWED_HOSTS is missing required provider hosts: "
            + ", ".join(missing)
        )


def _database_identity(value: str, key: str) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"{key} must use postgres:// or postgresql://")
    if not parsed.username:
        raise ValueError(f"{key} must include a database username")
    if not parsed.hostname:
        raise ValueError(f"{key} must include a database hostname")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError(f"{key} must include a database name")
    return parsed.username, parsed.hostname, parsed.port, database


def _bool_value(values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")



def _validate_positive_numeric(
    values: dict[str, str],
    errors: list[str],
    *,
    key: str,
    default: str,
    convert: Callable[[str], int | float],
    invalid_message: str,
    non_positive_message: str,
    require_finite: bool = False,
) -> None:
    raw = str(values.get(key) or default).strip()
    try:
        value = convert(raw)
    except ValueError:
        errors.append(invalid_message)
        return
    if require_finite and isinstance(value, float) and not math.isfinite(value):
        errors.append(non_positive_message)
        return
    if value <= 0:
        errors.append(non_positive_message)


def _validate_attachment_ocr(values: dict[str, str], errors: list[str]) -> None:
    try:
        _bool_value(values, "ATTACHMENT_OCR_ENABLED", False)
    except ValueError as exc:
        errors.append(str(exc))

    _validate_positive_numeric(
        values,
        errors,
        key="ATTACHMENT_OCR_TIMEOUT_SECONDS",
        default="30",
        convert=int,
        invalid_message="ATTACHMENT_OCR_TIMEOUT_SECONDS must be an integer",
        non_positive_message="ATTACHMENT_OCR_TIMEOUT_SECONDS must be greater than zero",
    )
    _validate_positive_numeric(
        values,
        errors,
        key="ATTACHMENT_PDF_OCR_SCALE",
        default="2.0",
        convert=float,
        invalid_message="ATTACHMENT_PDF_OCR_SCALE must be numeric",
        non_positive_message=(
            "ATTACHMENT_PDF_OCR_SCALE must be a finite number greater than zero"
        ),
        require_finite=True,
    )
    _validate_positive_numeric(
        values,
        errors,
        key="ATTACHMENT_PDF_OCR_MAX_PAGES",
        default="100",
        convert=int,
        invalid_message="ATTACHMENT_PDF_OCR_MAX_PAGES must be an integer",
        non_positive_message="ATTACHMENT_PDF_OCR_MAX_PAGES must be greater than zero",
    )
    _validate_positive_numeric(
        values,
        errors,
        key="ATTACHMENT_PARSER_TIMEOUT_SECONDS",
        default="45",
        convert=float,
        invalid_message="ATTACHMENT_PARSER_TIMEOUT_SECONDS must be numeric",
        non_positive_message=(
            "ATTACHMENT_PARSER_TIMEOUT_SECONDS must be a finite number greater than zero"
        ),
        require_finite=True,
    )
    _validate_positive_numeric(
        values,
        errors,
        key="ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS",
        default="45",
        convert=float,
        invalid_message="ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS must be numeric",
        non_positive_message=(
            "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS must be a finite number greater than zero"
        ),
        require_finite=True,
    )

def _validate_embedding_runtime(values: dict[str, str], errors: list[str]) -> None:
    try:
        runtime_enabled = _bool_value(values, "EMBEDDING_SPACE_RUNTIME_ENABLED", False)
    except ValueError as exc:
        errors.append(str(exc))
        return

    if not runtime_enabled:
        openai_key = values.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            errors.append("OPENAI_API_KEY is required when EMBEDDING_SPACE_RUNTIME_ENABLED is false")
        elif any(marker in openai_key.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("OPENAI_API_KEY still contains a placeholder value")
        return

    provider = str(values.get("EMBEDDING_PROVIDER") or "bge").strip().casefold()
    if provider == "bge":
        model = str(values.get("BGE_EMBEDDING_MODEL") or BGE_MODEL).strip()
        if model != BGE_MODEL:
            errors.append(f"BGE_EMBEDDING_MODEL must be {BGE_MODEL!r}")

        artifact_path = str(values.get("BGE_EMBEDDING_PATH") or "").strip()
        if not artifact_path:
            errors.append(
                "BGE_EMBEDDING_PATH is required when BGE embeddings are active"
            )
        elif any(marker in artifact_path.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("BGE_EMBEDDING_PATH still contains a placeholder value")
        elif not PurePosixPath(artifact_path).is_absolute():
            errors.append("BGE_EMBEDDING_PATH must be an absolute path inside the worker container")
        return

    if provider == "cohere":
        try:
            external_allowed = _bool_value(values, "ALLOW_EXTERNAL_EMBEDDINGS", False)
        except ValueError as exc:
            errors.append(str(exc))
            return
        if not external_allowed:
            errors.append("Cohere embeddings require ALLOW_EXTERNAL_EMBEDDINGS=true")

        model = str(values.get("COHERE_EMBEDDING_MODEL") or COHERE_MODEL).strip()
        if model != COHERE_MODEL:
            errors.append(f"COHERE_EMBEDDING_MODEL must be {COHERE_MODEL!r}")

        api_key = str(values.get("COHERE_API_KEY") or "").strip()
        if not api_key:
            errors.append("COHERE_API_KEY is required when Cohere embeddings are active")
        elif any(marker in api_key.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("COHERE_API_KEY still contains a placeholder value")
        return

    errors.append("EMBEDDING_PROVIDER must be 'bge' or 'cohere'")



def _validate_reranker(values: dict[str, str], errors: list[str]) -> None:
    try:
        enabled = _bool_value(values, "RERANKER_ENABLED", False)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not enabled:
        return

    provider = str(values.get("RERANKER_PROVIDER") or "bge").strip().casefold()
    if provider == "bge":
        model = str(values.get("RERANKER_MODEL") or BGE_RERANKER_MODEL).strip()
        if model != BGE_RERANKER_MODEL:
            errors.append(f"RERANKER_MODEL must be {BGE_RERANKER_MODEL!r}")
        artifact_path = str(values.get("BGE_RERANKER_PATH") or "").strip()
        if not artifact_path:
            errors.append("BGE_RERANKER_PATH is required when BGE reranking is active")
        elif any(marker in artifact_path.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("BGE_RERANKER_PATH still contains a placeholder value")
        elif not Path(artifact_path).is_absolute():
            errors.append("BGE_RERANKER_PATH must be an absolute path inside the worker container")
        return

    if provider == "cohere":
        try:
            allowed = _bool_value(values, "ALLOW_EXTERNAL_RERANKER", False)
        except ValueError as exc:
            errors.append(str(exc))
            return
        if not allowed:
            errors.append("Cohere reranking requires ALLOW_EXTERNAL_RERANKER=true")
        model = str(values.get("COHERE_RERANKER_MODEL") or COHERE_RERANKER_MODEL).strip()
        if model != COHERE_RERANKER_MODEL:
            errors.append(f"COHERE_RERANKER_MODEL must be {COHERE_RERANKER_MODEL!r}")
        key = str(values.get("COHERE_API_KEY") or "").strip()
        if not key:
            errors.append("COHERE_API_KEY is required when Cohere reranking is active")
        elif any(marker in key.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("COHERE_API_KEY still contains a placeholder value")
        return

    errors.append("RERANKER_PROVIDER must be 'bge' or 'cohere'")



def _validate_judit_callback(values: dict[str, str], errors: list[str]) -> None:
    value = str(values.get("JUDIT_CALLBACK_URL") or "").strip()
    if not value:
        return

    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append("JUDIT_CALLBACK_URL must be an absolute HTTPS URL")
        return
    if parsed.username is not None or parsed.password is not None:
        errors.append("JUDIT_CALLBACK_URL must not contain URL credentials")
    if parsed.query or parsed.fragment:
        errors.append("JUDIT_CALLBACK_URL must not contain query parameters or fragments")
    if parsed.port not in {None, 443}:
        errors.append("JUDIT_CALLBACK_URL must use the standard HTTPS port")

    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        errors.append("JUDIT_CALLBACK_URL must use a public DNS hostname")
    else:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            errors.append("JUDIT_CALLBACK_URL must use a public DNS hostname")

    webhook_token = str(values.get("JUDIT_WEBHOOK_TOKEN") or "")
    if webhook_token and parsed.path != f"/webhooks/judit/{webhook_token}":
        errors.append(
            "JUDIT_CALLBACK_URL path must match /webhooks/judit/<JUDIT_WEBHOOK_TOKEN>"
        )


def _validate_datajud(values: dict[str, str], errors: list[str]) -> None:
    try:
        enabled = _bool_value(values, "DATAJUD_ENABLED", False)
        authorized = _bool_value(values, "DATAJUD_AUTHORIZED_USE", False)
    except ValueError as exc:
        errors.append(str(exc))
        return

    if not enabled:
        return
    if not authorized:
        errors.append("DATAJUD_ENABLED requires DATAJUD_AUTHORIZED_USE=true")

    api_key = str(values.get("DATAJUD_API_KEY") or "").strip()
    if not api_key:
        errors.append("DATAJUD_API_KEY is required when DataJud enrichment is active")
    elif any(marker in api_key.lower() for marker in PLACEHOLDER_MARKERS):
        errors.append("DATAJUD_API_KEY still contains a placeholder value")

    base_url = str(
        values.get("DATAJUD_BASE_URL") or "https://api-publica.datajud.cnj.jus.br"
    ).strip().rstrip("/")
    if not base_url.startswith("https://"):
        errors.append("DATAJUD_BASE_URL must use https")

    _validate_positive_numeric(
        values,
        errors,
        key="DATAJUD_TIMEOUT_SECONDS",
        default="20",
        convert=float,
        invalid_message="DATAJUD_TIMEOUT_SECONDS must be numeric",
        non_positive_message=(
            "DATAJUD_TIMEOUT_SECONDS must be a finite number greater than zero"
        ),
        require_finite=True,
    )



def _validate_browser_origins(values: dict[str, str], errors: list[str]) -> None:
    try:
        parse_browser_origins(values.get("RPY_BROWSER_ORIGINS"))
    except ValueError as exc:
        errors.append(str(exc))


def _validate_langfuse(values: dict[str, str], errors: list[str]) -> None:
    try:
        enabled = _bool_value(values, "LANGFUSE_ENABLED", False)
    except ValueError as exc:
        errors.append(str(exc))
        return

    if not enabled:
        return

    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        value = str(values.get(key) or "").strip()
        if not value:
            errors.append(f"{key} is required when Langfuse tracing is active")
        elif any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append(f"{key} still contains a placeholder value")

    base_url = str(values.get("LANGFUSE_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        errors.append("LANGFUSE_BASE_URL is required when Langfuse tracing is active")
    elif not base_url.startswith("https://"):
        errors.append("LANGFUSE_BASE_URL must use https in production")

    environment = str(values.get("LANGFUSE_TRACING_ENVIRONMENT") or "production").strip()
    if not LANGFUSE_ENVIRONMENT_RE.fullmatch(environment):
        errors.append(
            "LANGFUSE_TRACING_ENVIRONMENT must be 1-40 lowercase letters, numbers, "
            "hyphens or underscores and must not start with 'langfuse'"
        )


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_KEYS:
        value = values.get(key, "").strip()
        if not value:
            errors.append(f"{key} is required")
            continue
        if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append(f"{key} still contains a placeholder value")

    for key in ("JUDIT_WEBHOOK_TOKEN", "RPY_OPS_TOKEN"):
        raw_token = str(values.get(key) or "")
        if any(character.isspace() for character in raw_token):
            errors.append(f"{key} must not contain whitespace")

    _validate_positive_numeric(
        values,
        errors,
        key="JUDIT_WEBHOOK_MAX_BODY_BYTES",
        default="5242880",
        convert=int,
        invalid_message="JUDIT_WEBHOOK_MAX_BODY_BYTES must be an integer",
        non_positive_message="JUDIT_WEBHOOK_MAX_BODY_BYTES must be greater than zero",
    )
    _validate_attachment_ocr(values, errors)
    _validate_embedding_runtime(values, errors)
    _validate_reranker(values, errors)
    _validate_judit_callback(values, errors)
    _validate_browser_origins(values, errors)
    _validate_datajud(values, errors)
    _validate_langfuse(values, errors)
    _validate_positive_numeric(
        values,
        errors,
        key="EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS",
        default="10",
        convert=float,
        invalid_message="EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must be numeric",
        non_positive_message=(
            "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must be a finite number greater than zero"
        ),
        require_finite=True,
    )
    _validate_egress_allowlist(values, errors)

    image = values.get("RPY_IMAGE", "").strip()
    if image and not DIGEST_RE.fullmatch(image):
        errors.append("RPY_IMAGE must be an immutable sha256 registry digest")

    identities = {}
    for key in DB_URL_KEYS:
        value = values.get(key, "").strip()
        if not value:
            continue
        try:
            identities[key] = _database_identity(value, key)
        except ValueError as exc:
            errors.append(str(exc))
    if len(identities) == len(DB_URL_KEYS):
        usernames = {key: identity[0] for key, identity in identities.items()}
        if len(set(usernames.values())) != len(usernames):
            duplicates = {}
            for key, username in usernames.items():
                duplicates.setdefault(username, []).append(key)
            for username, keys in sorted(duplicates.items()):
                if len(keys) > 1:
                    errors.append(
                        f"database role {username!r} is reused by: {', '.join(sorted(keys))}"
                    )
        if len({(host, port, database) for _, host, port, database in identities.values()}) != 1:
            errors.append("all database URLs must target the same PostgreSQL database")

    bearer_raw = values.get("RPY_BEARER_TOKENS", "").strip()
    if bearer_raw:
        try:
            bearer_tokens = json.loads(
                bearer_raw,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            if not isinstance(bearer_tokens, dict) or not bearer_tokens:
                raise ValueError("must be a non-empty JSON object")
            for token, tenant_id in bearer_tokens.items():
                if not isinstance(token, str) or not token:
                    raise ValueError("bearer token keys must be non-empty strings")
                if token != token.strip() or any(char.isspace() for char in token):
                    raise ValueError("legacy bearer tokens must not contain whitespace")
                if "," in token:
                    raise ValueError("legacy bearer tokens must not contain commas")
                if token.startswith(("sk_live_", "sk_test_")):
                    raise ValueError(
                        "legacy bearer tokens must not use sk_live_ or sk_test_ prefixes"
                    )
                UUID(str(tenant_id))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"RPY_BEARER_TOKENS is invalid: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production deploy environment invariants")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Read values from a KEY=VALUE file instead of the current process environment",
    )
    args = parser.parse_args()
    values = _load_env_file(args.env_file) if args.env_file else dict(os.environ)
    errors = validate(values)
    if errors:
        for error in errors:
            print(f"deploy preflight: {error}")
        return 1
    print("deploy preflight: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
