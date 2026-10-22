import pytest
from hypothesis import given, settings, strategies as st

from app.log_safety import REDACTED, sanitize_error_message


def test_sanitize_error_message_redacts_configured_secret(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-provider-value")

    rendered = sanitize_error_message(
        "provider rejected api key sk-secret-provider-value"
    )

    assert "sk-secret-provider-value" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_all_provider_api_keys(monkeypatch) -> None:
    secrets = {
        "JUDIT_API_KEY": "judit-provider-secret",
        "DATAJUD_API_KEY": "datajud-provider-secret",
        "COHERE_API_KEY": "cohere-provider-secret",
        "LANGFUSE_SECRET_KEY": "langfuse-observability-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    rendered = sanitize_error_message(" ".join(secrets.values()))

    for value in secrets.values():
        assert value not in rendered
    assert rendered.count(REDACTED) == len(secrets)


def test_sanitize_error_message_redacts_uri_password_and_bearer() -> None:
    rendered = sanitize_error_message(
        "failed postgresql://rpy_worker:db-super-secret@postgres:5432/rpy "
        "Authorization: Bearer opaque-token-value"
    )

    assert "db-super-secret" not in rendered
    assert "opaque-token-value" not in rendered
    assert "postgresql://rpy_worker:[REDACTED]@postgres:5432/rpy" in rendered
    assert "Authorization: Bearer [REDACTED]" in rendered


def test_sanitize_error_message_redacts_empty_user_uri_passwords() -> None:
    rendered = sanitize_error_message(
        "failed postgresql://:db-super-secret@postgres:5432/rpy "
        "redis://:topsecret@redis.internal:6379/0"
    )

    assert "db-super-secret" not in rendered
    assert "topsecret" not in rendered
    assert "postgresql://:[REDACTED]@postgres:5432/rpy" in rendered
    assert "redis://:[REDACTED]@redis.internal:6379/0" in rendered


def test_sanitize_error_message_is_bounded() -> None:
    rendered = sanitize_error_message("x" * 5000, max_chars=120)

    assert len(rendered) == 120
    assert rendered.endswith("… [truncated]")


def test_sanitize_error_message_redacts_api_key_tokens() -> None:
    rendered = sanitize_error_message(
        "Failed authentication for sk_live_abc1234567890abcdef and sk_test_xyz9876543210fedcba"
    )

    assert "sk_live_abc1234567890abcdef" not in rendered
    assert "sk_test_xyz9876543210fedcba" not in rendered
    assert rendered == f"Failed authentication for {REDACTED} and {REDACTED}"


def test_sanitize_error_message_redacts_anthropic_api_key_token() -> None:
    rendered = sanitize_error_message(
        "provider failed for sk-ant-api03-abcdef1234567890-xyz"
    )

    assert "sk-ant-api03-abcdef1234567890-xyz" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_jwt_tokens() -> None:
    jwt_token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    rendered = sanitize_error_message(f"invalid session token {jwt_token} in request")

    assert jwt_token not in rendered
    assert f"invalid session token {REDACTED} in request" == rendered


def test_sanitize_error_message_redacts_json_bearer_token_key(monkeypatch) -> None:
    monkeypatch.setenv(
        "RPY_BEARER_TOKENS",
        '{"legacy-secret-token-123":"00000000-0000-0000-0000-000000000000"}',
    )

    rendered = sanitize_error_message("failed using legacy-secret-token-123")

    assert "legacy-secret-token-123" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_demo_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("RPY_DEMO_BEARER_TOKEN", "demo-secret-token-456")

    rendered = sanitize_error_message("failed using demo-secret-token-456")

    assert "demo-secret-token-456" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_pgpassword(monkeypatch) -> None:
    monkeypatch.setenv("PGPASSWORD", "pg-secret-pass-789")

    rendered = sanitize_error_message("failed connection with pg-secret-pass-789")

    assert "pg-secret-pass-789" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_judit_callback_url(monkeypatch) -> None:
    callback_url = "https://staging.example.invalid/webhooks/judit/secret-callback-token-789"
    monkeypatch.setenv("JUDIT_CALLBACK_URL", callback_url)

    rendered = sanitize_error_message(f"callback request to {callback_url} failed")

    assert callback_url not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_structured_bearer_credentials() -> None:
    rendered = sanitize_error_message(
        'request failed with bearer: opaque-bearer-token-123 and "bearer": "opaque-bearer-token-456"'
    )

    assert "opaque-bearer-token-123" not in rendered
    assert "opaque-bearer-token-456" not in rendered
    assert f"bearer: {REDACTED}" in rendered
    assert f'"bearer": "{REDACTED}"' in rendered


def test_sanitize_error_message_redacts_compound_secret_keys() -> None:
    rendered = sanitize_error_message(
        "failed with secret_key=secret_val_123, private_key=priv_val_456, "
        "client_secret=client_val_789 and refresh_token=refresh_val_012"
    )

    assert "secret_val_123" not in rendered
    assert "priv_val_456" not in rendered
    assert "client_val_789" not in rendered
    assert "refresh_val_012" not in rendered
    assert "secret_key=[REDACTED]" in rendered
    assert "private_key=[REDACTED]" in rendered
    assert "client_secret=[REDACTED]" in rendered
    assert "refresh_token=[REDACTED]" in rendered


@pytest.mark.parametrize("scheme", ["Bearer", "APIKey", "Basic", "Token", "Digest", "Negotiate", "OAuth"])
def test_sanitize_error_message_redacts_authorization_schemes(scheme: str) -> None:
    rendered = sanitize_error_message(
        f"Authorization: {scheme} opaque-secret-credential"
    )

    assert "opaque-secret-credential" not in rendered
    assert f"Authorization: {scheme} {REDACTED}" in rendered


@pytest.mark.parametrize("scheme", ["Bearer", "APIKey", "Basic", "Token", "Digest", "Negotiate", "OAuth"])
def test_sanitize_error_message_redacts_nested_authorization_headers(scheme: str) -> None:
    secret = f"SYNTH_NESTED_AUTH_{scheme}_281"
    message = (
        '{"error":{"headers":{"Authorization":"'
        f"{scheme} {secret}"
        '"}},"status":502,"request_id":"fixture-281"}'
    )

    rendered = sanitize_error_message(message)

    if secret in rendered:
        raise AssertionError("nested Authorization credential was not redacted")
    for diagnostic in (
        '"headers":{"Authorization":',
        REDACTED,
        '"status":502',
        '"request_id":"fixture-281"',
    ):
        if diagnostic not in rendered:
            raise AssertionError(f"sanitizer lost expected diagnostic: {diagnostic}")


@pytest.mark.parametrize(
    "message",
    [
        '{"Authorization":"Basic"}',
        'Authorization: Basic',
        '{"Authorization":"UnknownScheme benign"}',
        'Authorization: Bearerless benign-text',
        'The word Basic authentication succeeded; status=200 request_id=fixture',
    ],
)
def test_sanitize_error_message_preserves_noncredential_authorization_text(message: str) -> None:
    if sanitize_error_message(message) != message:
        raise AssertionError("noncredential Authorization text changed")


def test_sanitize_error_message_redacts_quoted_authorization_value() -> None:
    rendered = sanitize_error_message('Authorization: "OAuth SYNTH_QUOTED_AUTH_281"')

    expected = f'Authorization: "OAuth {REDACTED}"'
    if "SYNTH_QUOTED_AUTH_281" in rendered or rendered != expected:
        raise AssertionError("quoted Authorization value was not safely redacted")


def test_sanitize_error_message_redacts_quoted_digest_directives() -> None:
    import json

    message = (
        '{"error":{"headers":{"Authorization":"Digest username=\\"alice\\", '
        'realm=\\"sample\\", response=\\"SYNTH_DIGEST_RESPONSE_281\\", '
        'qop=auth, nc=00000001, cnonce=\\"SYNTH_DIGEST_CNONCE_281\\""}},'
        '"status":401,"request_id":"fixture-digest"}'
    )
    rendered = sanitize_error_message(message)

    try:
        parsed = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise AssertionError("redacted nested Authorization value is not valid JSON") from exc
    if "SYNTH_DIGEST_RESPONSE_281" in rendered or "SYNTH_DIGEST_CNONCE_281" in rendered:
        raise AssertionError("quoted Digest credential directives were not redacted")
    if parsed["status"] != 401 or parsed["request_id"] != "fixture-digest":
        raise AssertionError("sanitizer lost public diagnostics")
    if parsed["error"]["headers"]["Authorization"] != f"Digest {REDACTED}":
        raise AssertionError("Digest header was not replaced as one credential value")


def test_sanitize_error_message_redacts_raw_digest_line_only() -> None:
    message = (
        'Authorization: Digest username="alice", response="SYNTH_RAW_DIGEST_281", qop=auth\n'
        "status=401 request_id=fixture-raw-digest"
    )
    rendered = sanitize_error_message(message)
    expected = f"Authorization: Digest {REDACTED}\nstatus=401 request_id=fixture-raw-digest"

    if "SYNTH_RAW_DIGEST_281" in rendered or rendered != expected:
        raise AssertionError("raw Digest line leaked credentials or lost following diagnostics")


def test_sanitize_error_message_handles_case_and_malformed_authorization_headers() -> None:
    message = '{"headers":{"aUtHoRiZaTiOn":"bAsIc SYNTH_CASE_AUTH_281"}}'
    rendered = sanitize_error_message(message)
    if "SYNTH_CASE_AUTH_281" in rendered:
        raise AssertionError("case-insensitive nested Authorization value was not redacted")

    malformed = '{"Authorization":"Bearer SYNTH_UNCLOSED_AUTH_281}'
    rendered_malformed = sanitize_error_message(malformed)
    if "SYNTH_UNCLOSED_AUTH_281" in rendered_malformed:
        raise AssertionError("malformed nested Authorization value retained a credential")


def test_sanitize_error_message_preserves_already_redacted_authorization() -> None:
    message = '{"Authorization":"Bearer [REDACTED]"}'
    if sanitize_error_message(message) != message:
        raise AssertionError("sanitizing an existing marker changed the value")


_CREDENTIAL_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


@settings(max_examples=120, deadline=None)
@given(
    key=st.sampled_from((
        "api_key", "api-key", "token", "access_token", "bearer", "password", "secret",
        "secret_key", "private_key", "client_secret", "refresh_token", "auth_token", "access_key",
    )),
    key_quote=st.sampled_from(("", '"', "'")),
    value_quote=st.sampled_from(("", '"', "'")),
    secret=st.text(alphabet=_CREDENTIAL_ALPHABET, min_size=12, max_size=48),
)
def test_sanitize_error_message_redacts_structured_key_value_credentials(
    key: str,
    key_quote: str,
    value_quote: str,
    secret: str,
) -> None:
    rendered = sanitize_error_message(
        f"{key_quote}{key}{key_quote}: {value_quote}{secret}{value_quote}"
    )

    assert secret not in rendered  # nosec B101
    assert REDACTED in rendered  # nosec B101


@settings(max_examples=100, deadline=None)
@given(
    prefix=st.sampled_from(("sk-proj-", "sk-svcacct-", "sk-ant-api03-", "sk-")),
    suffix=st.text(alphabet=_CREDENTIAL_ALPHABET, min_size=12, max_size=48),
)
def test_sanitize_error_message_redacts_standalone_sk_token_families(
    prefix: str,
    suffix: str,
) -> None:
    token = prefix + suffix
    rendered = sanitize_error_message(f"provider exception credential={token}")

    assert token not in rendered  # nosec B101
    assert REDACTED in rendered  # nosec B101
