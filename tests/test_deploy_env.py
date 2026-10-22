from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_deploy_env.py"
spec = importlib.util.spec_from_file_location("validate_deploy_env", MODULE_PATH)
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def _valid_values() -> dict[str, str]:
    return {
        "RPY_IMAGE": "ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64,
        "POSTGRES_PASSWORD": "prod-password",
        "MIGRATION_DATABASE_URL": "postgresql://rpy:pw@postgres:5432/rpy",
        "API_DATABASE_URL": "postgresql://rpy_api:pw@postgres:5432/rpy",
        "WORKER_DATABASE_URL": "postgresql://rpy_worker:pw@postgres:5432/rpy",
        "SCHEDULER_DATABASE_URL": "postgresql://rpy_scheduler:pw@postgres:5432/rpy",
        "BACKUP_DATABASE_URL": "postgresql://rpy_backup:pw@postgres:5432/rpy",
        "ANTHROPIC_API_KEY": "anthropic-key",
        "OPENAI_API_KEY": "openai-key",
        "JUDIT_API_KEY": "judit-api-key",
        "JUDIT_CALLBACK_URL": "https://rpy.test/webhooks/judit/judit-token",
        "JUDIT_WEBHOOK_TOKEN": "judit-token",
        "RPY_BEARER_TOKENS": '{"tenant-token":"00000000-0000-0000-0000-000000000001"}',
        "RPY_OPS_TOKEN": "ops-token",
        "EGRESS_PROXY_ALLOWED_HOSTS": ",".join(
            [
                "api.anthropic.com",
                "api.openai.com",
                "requests.prod.judit.io",
                "tracking.production.judit.io",
            ]
        ),
    }


def _enable_bge(values: dict[str, str]) -> None:
    values.pop("OPENAI_API_KEY", None)
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "true"
    values["EMBEDDING_PROVIDER"] = "bge"
    values["BGE_EMBEDDING_MODEL"] = "BAAI/bge-m3"
    values["BGE_EMBEDDING_PATH"] = "/opt/rpy/models/bge-m3"


def _enable_cohere(values: dict[str, str]) -> None:
    values.pop("OPENAI_API_KEY", None)
    values["EGRESS_PROXY_ALLOWED_HOSTS"] += ",api.cohere.com"
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "true"
    values["EMBEDDING_PROVIDER"] = "cohere"
    values["ALLOW_EXTERNAL_EMBEDDINGS"] = "true"
    values["COHERE_EMBEDDING_MODEL"] = "embed-v4.0"
    values["COHERE_API_KEY"] = "cohere-key"


def _require_error(values: dict[str, str], expected: str) -> None:
    errors = preflight.validate(values)
    if expected not in errors:
        raise AssertionError(f"expected deploy preflight error {expected!r}, got {errors!r}")


def test_valid_deploy_environment_passes() -> None:
    assert preflight.validate(_valid_values()) == []



def test_deploy_preflight_requires_judit_callback_url() -> None:
    values = _valid_values()
    values.pop("JUDIT_CALLBACK_URL")
    _require_error(values, "JUDIT_CALLBACK_URL is required")


def test_deploy_preflight_validates_judit_callback_contract() -> None:
    invalid = {
        "http://rpy.test/webhooks/judit/judit-token":
            "JUDIT_CALLBACK_URL must be an absolute HTTPS URL",
        "https://user:pass@rpy.test/webhooks/judit/judit-token":
            "JUDIT_CALLBACK_URL must not contain URL credentials",
        "https://rpy.test/webhooks/judit/judit-token?x=1":
            "JUDIT_CALLBACK_URL must not contain query parameters or fragments",
        "https://rpy.test:8443/webhooks/judit/judit-token":
            "JUDIT_CALLBACK_URL must use the standard HTTPS port",
        "https://localhost/webhooks/judit/judit-token":
            "JUDIT_CALLBACK_URL must use a public DNS hostname",
        "https://127.0.0.1/webhooks/judit/judit-token":
            "JUDIT_CALLBACK_URL must use a public DNS hostname",
        "https://rpy.test/webhooks/judit/other-token":
            "JUDIT_CALLBACK_URL path must match /webhooks/judit/<JUDIT_WEBHOOK_TOKEN>",
    }
    for callback_url, expected in invalid.items():
        values = _valid_values()
        values["JUDIT_CALLBACK_URL"] = callback_url
        _require_error(values, expected)

def test_deploy_preflight_accepts_github_pages_browser_origin() -> None:
    values = _valid_values()
    values["RPY_BROWSER_ORIGINS"] = "https://oigorbrito.github.io"
    assert preflight.validate(values) == []


def test_deploy_preflight_rejects_invalid_browser_origins() -> None:
    values = _valid_values()
    values["RPY_BROWSER_ORIGINS"] = "https://oigorbrito.github.io/rpy"
    _require_error(values, "RPY_BROWSER_ORIGINS entries must be bare HTTPS origins")

    values = _valid_values()
    values["RPY_BROWSER_ORIGINS"] = "https://*.github.io"
    _require_error(values, "RPY_BROWSER_ORIGINS must not contain wildcards")


def test_deploy_preflight_validates_inbound_post_body_limit() -> None:
    for value, expected in (
        ("not-an-integer", "JUDIT_WEBHOOK_MAX_BODY_BYTES must be an integer"),
        ("0", "JUDIT_WEBHOOK_MAX_BODY_BYTES must be greater than zero"),
        ("-1", "JUDIT_WEBHOOK_MAX_BODY_BYTES must be greater than zero"),
    ):
        values = _valid_values()
        values["JUDIT_WEBHOOK_MAX_BODY_BYTES"] = value
        _require_error(values, expected)


def test_deploy_preflight_rejects_non_finite_attachment_pdf_ocr_scale() -> None:
    for value in ("nan", "inf", "-inf"):
        values = _valid_values()
        values["ATTACHMENT_PDF_OCR_SCALE"] = value
        _require_error(
            values,
            "ATTACHMENT_PDF_OCR_SCALE must be a finite number greater than zero",
        )


def test_deploy_preflight_rejects_non_finite_attachment_parser_timeouts() -> None:
    for key in (
        "ATTACHMENT_PARSER_TIMEOUT_SECONDS",
        "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS",
    ):
        for value in ("nan", "inf", "-inf"):
            values = _valid_values()
            values[key] = value
            _require_error(
                values,
                f"{key} must be a finite number greater than zero",
            )


def test_deploy_preflight_validates_attachment_ocr_integer_controls() -> None:
    values = _valid_values()
    values["ATTACHMENT_OCR_TIMEOUT_SECONDS"] = "0"
    values["ATTACHMENT_PDF_OCR_MAX_PAGES"] = "many"
    errors = preflight.validate(values)

    if "ATTACHMENT_OCR_TIMEOUT_SECONDS must be greater than zero" not in errors:
        raise AssertionError(f"missing timeout validation error: {errors!r}")
    if "ATTACHMENT_PDF_OCR_MAX_PAGES must be an integer" not in errors:
        raise AssertionError(f"missing max-pages validation error: {errors!r}")


def test_bge_runtime_does_not_require_openai_key() -> None:
    values = _valid_values()
    _enable_bge(values)
    assert preflight.validate(values) == []


def test_bge_runtime_requires_local_artifact_path() -> None:
    values = _valid_values()
    _enable_bge(values)
    values.pop("BGE_EMBEDDING_PATH")
    assert "BGE_EMBEDDING_PATH is required when BGE embeddings are active" in preflight.validate(values)


def test_bge_runtime_requires_absolute_artifact_path() -> None:
    values = _valid_values()
    _enable_bge(values)
    values["BGE_EMBEDDING_PATH"] = "models/bge-m3"
    assert (
        "BGE_EMBEDDING_PATH must be an absolute path inside the worker container"
        in preflight.validate(values)
    )


def test_cohere_runtime_requires_explicit_authorization() -> None:
    values = _valid_values()
    _enable_cohere(values)
    values["ALLOW_EXTERNAL_EMBEDDINGS"] = "false"
    assert "Cohere embeddings require ALLOW_EXTERNAL_EMBEDDINGS=true" in preflight.validate(values)


def test_authorized_cohere_runtime_does_not_require_openai_or_bge_path() -> None:
    values = _valid_values()
    _enable_cohere(values)
    values.pop("BGE_EMBEDDING_PATH", None)
    assert preflight.validate(values) == []


def test_cohere_runtime_requires_key() -> None:
    values = _valid_values()
    _enable_cohere(values)
    values.pop("COHERE_API_KEY")
    assert "COHERE_API_KEY is required when Cohere embeddings are active" in preflight.validate(values)


def test_cohere_runtime_rejects_wrong_model() -> None:
    values = _valid_values()
    _enable_cohere(values)
    values["COHERE_EMBEDDING_MODEL"] = "embed-v3.0"
    assert "COHERE_EMBEDDING_MODEL must be 'embed-v4.0'" in preflight.validate(values)


def test_legacy_runtime_still_requires_openai_key() -> None:
    values = _valid_values()
    values.pop("OPENAI_API_KEY")
    errors = preflight.validate(values)
    assert (
        "OPENAI_API_KEY is required when EMBEDDING_SPACE_RUNTIME_ENABLED is false"
        in errors
    )


def test_bge_runtime_rejects_wrong_model() -> None:
    values = _valid_values()
    _enable_bge(values)
    values["BGE_EMBEDDING_MODEL"] = "other/model"
    assert "BGE_EMBEDDING_MODEL must be 'BAAI/bge-m3'" in preflight.validate(values)


def test_embedding_runtime_flag_must_be_boolean() -> None:
    values = _valid_values()
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "sometimes"
    assert "EMBEDDING_SPACE_RUNTIME_ENABLED must be a boolean" in preflight.validate(values)


def test_mutable_image_reference_is_rejected() -> None:
    values = _valid_values()
    values["RPY_IMAGE"] = "ghcr.io/oigorbrito/rpy:v1.0.0"
    assert "RPY_IMAGE must be an immutable sha256 registry digest" in preflight.validate(values)


def test_runtime_database_role_cannot_reuse_migration_identity() -> None:
    values = _valid_values()
    values["API_DATABASE_URL"] = "postgresql://rpy:other@postgres:5432/rpy"
    errors = preflight.validate(values)
    assert any("database role 'rpy' is reused" in error for error in errors)


def test_database_urls_must_target_same_database() -> None:
    values = _valid_values()
    values["BACKUP_DATABASE_URL"] = "postgresql://rpy_backup:pw@other-host:5432/rpy"
    assert "all database URLs must target the same PostgreSQL database" in preflight.validate(values)


def test_deploy_preflight_rejects_whitespace_in_http_tokens() -> None:
    for key in ("JUDIT_WEBHOOK_TOKEN", "RPY_OPS_TOKEN"):
        for value in (" token", "token ", "token value", "token\tvalue", "token\nvalue"):
            values = _valid_values()
            values[key] = value
            _require_error(values, f"{key} must not contain whitespace")


def test_placeholder_values_are_rejected() -> None:
    values = _valid_values()
    values["ANTHROPIC_API_KEY"] = "replace-with-anthropic-key"
    assert "ANTHROPIC_API_KEY still contains a placeholder value" in preflight.validate(values)


def test_judit_api_key_is_required() -> None:
    values = _valid_values()
    values.pop("JUDIT_API_KEY")
    assert "JUDIT_API_KEY is required" in preflight.validate(values)


def test_deploy_preflight_rejects_ambiguous_legacy_bearer_prefixes() -> None:
    tenant_id = "00000000-0000-0000-0000-000000000001"
    for prefix in ("sk_live_", "sk_test_"):
        token = prefix + "legacytoken"
        values = _valid_values()
        values["RPY_BEARER_TOKENS"] = f'{{"{token}":"{tenant_id}"}}'
        _require_error(
            values,
            "RPY_BEARER_TOKENS is invalid: "
            "legacy bearer tokens must not use sk_live_ or sk_test_ prefixes",
        )


def test_deploy_preflight_rejects_duplicate_bearer_token_keys() -> None:
    values = _valid_values()
    values["RPY_BEARER_TOKENS"] = (
        '{"tenant-token":"00000000-0000-0000-0000-000000000001",'
        '"tenant-token":"00000000-0000-0000-0000-000000000002"}'
    )
    errors = preflight.validate(values)
    if not any(
        error.startswith("RPY_BEARER_TOKENS is invalid: duplicate JSON object key")
        for error in errors
    ):
        raise AssertionError(f"missing duplicate bearer token validation error: {errors!r}")


def test_deploy_preflight_rejects_comma_bearing_legacy_token() -> None:
    values = _valid_values()
    values["RPY_BEARER_TOKENS"] = (
        '{"tenant,other":"00000000-0000-0000-0000-000000000001"}'
    )
    errors = preflight.validate(values)
    if not any("legacy bearer tokens must not contain commas" in error for error in errors):
        raise AssertionError(f"missing comma-bearing token validation error: {errors!r}")


def test_bearer_mapping_requires_tenant_uuid() -> None:
    values = _valid_values()
    values["RPY_BEARER_TOKENS"] = '{"tenant-token":"not-a-uuid"}'
    assert any(error.startswith("RPY_BEARER_TOKENS is invalid:") for error in preflight.validate(values))


def test_env_file_parser_rejects_duplicate_keys(tmp_path: Path) -> None:
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "RPY_OPS_TOKEN=first\nRPY_OPS_TOKEN=second\n",
        encoding="utf-8",
    )
    try:
        preflight._load_env_file(env_file)
    except ValueError as exc:
        if str(exc) != "duplicate environment variable: RPY_OPS_TOKEN":
            raise AssertionError(f"unexpected duplicate env error: {exc}") from exc
    else:
        raise AssertionError("duplicate environment variables must be rejected")


def test_env_file_parser_rejects_empty_variable_name(tmp_path: Path) -> None:
    env_file = tmp_path / "production.env"
    env_file.write_text("=value\n", encoding="utf-8")
    try:
        preflight._load_env_file(env_file)
    except ValueError as exc:
        if str(exc) != "environment variable name must not be empty":
            raise AssertionError(f"unexpected empty-name error: {exc}") from exc
    else:
        raise AssertionError("empty environment variable names must be rejected")


def test_env_file_parser_accepts_export_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "# comment\nexport RPY_IMAGE='ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64 + "'\n",
        encoding="utf-8",
    )
    assert preflight._load_env_file(env_file)["RPY_IMAGE"].endswith("a" * 64)


def _enable_bge_reranker(values: dict[str, str]) -> None:
    values["RERANKER_ENABLED"] = "true"
    values["RERANKER_PROVIDER"] = "bge"
    values["RERANKER_MODEL"] = "BAAI/bge-reranker-v2-m3"
    values["BGE_RERANKER_PATH"] = "/opt/rpy/models/bge-reranker-v2-m3"


def _enable_cohere_reranker(values: dict[str, str]) -> None:
    values["RERANKER_ENABLED"] = "true"
    if "api.cohere.com" not in values["EGRESS_PROXY_ALLOWED_HOSTS"]:
        values["EGRESS_PROXY_ALLOWED_HOSTS"] += ",api.cohere.com"
    values["RERANKER_PROVIDER"] = "cohere"
    values["ALLOW_EXTERNAL_RERANKER"] = "true"
    values["COHERE_RERANKER_MODEL"] = "rerank-v4.0-pro"
    values["COHERE_API_KEY"] = "cohere-key"


def test_disabled_reranker_needs_no_extra_credentials() -> None:
    values = _valid_values()
    values["RERANKER_ENABLED"] = "false"
    assert preflight.validate(values) == []


def test_bge_reranker_requires_local_artifact_path() -> None:
    values = _valid_values()
    _enable_bge_reranker(values)
    values.pop("BGE_RERANKER_PATH")
    assert "BGE_RERANKER_PATH is required when BGE reranking is active" in preflight.validate(values)


def test_bge_reranker_requires_absolute_artifact_path() -> None:
    values = _valid_values()
    _enable_bge_reranker(values)
    values["BGE_RERANKER_PATH"] = "models/bge-reranker-v2-m3"
    assert (
        "BGE_RERANKER_PATH must be an absolute path inside the worker container"
        in preflight.validate(values)
    )


def test_cohere_reranker_requires_separate_authorization() -> None:
    values = _valid_values()
    _enable_cohere_reranker(values)
    values["ALLOW_EXTERNAL_RERANKER"] = "false"
    assert "Cohere reranking requires ALLOW_EXTERNAL_RERANKER=true" in preflight.validate(values)


def test_cohere_reranker_requires_key() -> None:
    values = _valid_values()
    _enable_cohere_reranker(values)
    values.pop("COHERE_API_KEY")
    assert "COHERE_API_KEY is required when Cohere reranking is active" in preflight.validate(values)


def test_cohere_reranker_rejects_wrong_model() -> None:
    values = _valid_values()
    _enable_cohere_reranker(values)
    values["COHERE_RERANKER_MODEL"] = "rerank-v3.5"
    assert "COHERE_RERANKER_MODEL must be 'rerank-v4.0-pro'" in preflight.validate(values)


def _enable_datajud(values: dict[str, str]) -> None:
    values["DATAJUD_ENABLED"] = "true"
    values["EGRESS_PROXY_ALLOWED_HOSTS"] += ",api-publica.datajud.cnj.jus.br"
    values["DATAJUD_AUTHORIZED_USE"] = "true"
    values["DATAJUD_API_KEY"] = "datajud-public-key"
    values["DATAJUD_BASE_URL"] = "https://api-publica.datajud.cnj.jus.br"
    values["DATAJUD_TIMEOUT_SECONDS"] = "20"


def test_disabled_datajud_requires_no_key_or_authorization() -> None:
    values = _valid_values()
    values["DATAJUD_ENABLED"] = "false"
    assert preflight.validate(values) == []


def test_datajud_requires_explicit_authorized_use() -> None:
    values = _valid_values()
    _enable_datajud(values)
    values["DATAJUD_AUTHORIZED_USE"] = "false"
    assert "DATAJUD_ENABLED requires DATAJUD_AUTHORIZED_USE=true" in preflight.validate(values)


def test_datajud_requires_key_when_enabled() -> None:
    values = _valid_values()
    _enable_datajud(values)
    values.pop("DATAJUD_API_KEY")
    assert (
        "DATAJUD_API_KEY is required when DataJud enrichment is active"
        in preflight.validate(values)
    )


def test_datajud_requires_https_base_url() -> None:
    values = _valid_values()
    _enable_datajud(values)
    values["DATAJUD_BASE_URL"] = "http://api-publica.datajud.cnj.jus.br"
    assert "DATAJUD_BASE_URL must use https" in preflight.validate(values)


def test_datajud_requires_positive_numeric_timeout() -> None:
    values = _valid_values()
    _enable_datajud(values)
    values["DATAJUD_TIMEOUT_SECONDS"] = "0"
    assert (
        "DATAJUD_TIMEOUT_SECONDS must be a finite number greater than zero"
        in preflight.validate(values)
    )


def test_datajud_rejects_non_finite_timeout() -> None:
    for value in ("nan", "inf", "-inf"):
        values = _valid_values()
        _enable_datajud(values)
        values["DATAJUD_TIMEOUT_SECONDS"] = value
        _require_error(
            values,
            "DATAJUD_TIMEOUT_SECONDS must be a finite number greater than zero",
        )


def _enable_langfuse(values: dict[str, str]) -> None:
    values["LANGFUSE_ENABLED"] = "true"
    values["EGRESS_PROXY_ALLOWED_HOSTS"] += ",langfuse.example.internal"
    values["LANGFUSE_PUBLIC_KEY"] = "pk-lf-production"
    values["LANGFUSE_SECRET_KEY"] = "sk-lf-production"
    values["LANGFUSE_BASE_URL"] = "https://langfuse.example.internal"
    values["LANGFUSE_TRACING_ENVIRONMENT"] = "production"


def test_disabled_langfuse_requires_no_credentials() -> None:
    values = _valid_values()
    values["LANGFUSE_ENABLED"] = "false"
    assert preflight.validate(values) == []


def test_langfuse_requires_credentials_when_enabled() -> None:
    values = _valid_values()
    _enable_langfuse(values)
    values.pop("LANGFUSE_SECRET_KEY")
    assert (
        "LANGFUSE_SECRET_KEY is required when Langfuse tracing is active"
        in preflight.validate(values)
    )


def test_langfuse_requires_https_base_url() -> None:
    values = _valid_values()
    _enable_langfuse(values)
    values["LANGFUSE_BASE_URL"] = "http://langfuse.internal"
    assert "LANGFUSE_BASE_URL must use https in production" in preflight.validate(values)


def test_langfuse_environment_must_match_sdk_contract() -> None:
    values = _valid_values()
    _enable_langfuse(values)
    values["LANGFUSE_TRACING_ENVIRONMENT"] = "Langfuse Production"
    assert any(
        error.startswith("LANGFUSE_TRACING_ENVIRONMENT must be")
        for error in preflight.validate(values)
    )



def test_deploy_preflight_rejects_non_finite_egress_timeout() -> None:
    for value in ("nan", "inf", "-inf"):
        values = _valid_values()
        values["EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS"] = value
        _require_error(
            values,
            "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must be a finite number greater than zero",
        )


def test_egress_allowlist_requires_core_provider_hosts() -> None:
    values = _valid_values()
    values["EGRESS_PROXY_ALLOWED_HOSTS"] = "api.anthropic.com"
    errors = preflight.validate(values)
    assert any(
        error.startswith("EGRESS_PROXY_ALLOWED_HOSTS is missing required provider hosts:")
        for error in errors
    )
    assert (
        "EGRESS_PROXY_ALLOWED_HOSTS is missing required provider hosts: "
        "api.openai.com, requests.prod.judit.io, tracking.production.judit.io"
        in errors
    )


def test_egress_allowlist_rejects_wildcards_and_ip_literals() -> None:
    values = _valid_values()
    values["EGRESS_PROXY_ALLOWED_HOSTS"] = "*.example.com"
    assert (
        "EGRESS_PROXY_ALLOWED_HOSTS contains an invalid hostname"
        in preflight.validate(values)
    )

    values["EGRESS_PROXY_ALLOWED_HOSTS"] = "127.0.0.1"
    assert (
        "EGRESS_PROXY_ALLOWED_HOSTS must contain DNS hostnames, not IP addresses"
        in preflight.validate(values)
    )


def test_legacy_embedding_requires_openai_host_in_egress_allowlist() -> None:
    values = _valid_values()
    values["EGRESS_PROXY_ALLOWED_HOSTS"] = (
        "api.anthropic.com,requests.prod.judit.io,tracking.production.judit.io"
    )
    assert (
        "EGRESS_PROXY_ALLOWED_HOSTS is missing required provider hosts: api.openai.com"
        in preflight.validate(values)
    )


def test_local_bge_does_not_require_openai_egress() -> None:
    values = _valid_values()
    _enable_bge(values)
    values["EGRESS_PROXY_ALLOWED_HOSTS"] = (
        "api.anthropic.com,requests.prod.judit.io,tracking.production.judit.io"
    )
    assert preflight.validate(values) == []


def test_enabled_langfuse_host_must_be_explicitly_allowlisted() -> None:
    values = _valid_values()
    _enable_langfuse(values)
    values["EGRESS_PROXY_ALLOWED_HOSTS"] = (
        "api.anthropic.com,api.openai.com,"
        "requests.prod.judit.io,tracking.production.judit.io"
    )
    assert (
        "EGRESS_PROXY_ALLOWED_HOSTS is missing required provider hosts: "
        "langfuse.example.internal"
        in preflight.validate(values)
    )
