from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError

import pytest

import app.datajud_client as client
from app.datajud_client import (
    DataJudConfig,
    datajud_alias_from_cnj,
    datajud_config,
    lookup_datajud_metadata,
)


@pytest.mark.parametrize(
    ("code", "alias"),
    [
        ("0000000-00.2026.4.04.0001", "trf4"),
        ("0000000-00.2026.5.04.0001", "trt4"),
        ("0000000-00.2026.6.21.0001", "tre-rs"),
        ("0000000-00.2026.8.21.0001", "tjrs"),
        ("07223914020178070001", "tjdft"),
        ("0000000-00.2026.9.21.0001", "tjmrs"),
        ("0000000-00.2026.5.00.0000", "tst"),
        ("0000000-00.2026.6.00.0000", "tse"),
        ("0000000-00.2026.7.00.0000", "stm"),
    ],
)
def test_datajud_alias_from_cnj_uses_segment_and_tribunal(code: str, alias: str) -> None:
    assert datajud_alias_from_cnj(code) == alias


def test_datajud_alias_rejects_unsupported_segment() -> None:
    with pytest.raises(ValueError, match="unsupported DataJud tribunal"):
        datajud_alias_from_cnj("0000000-00.2026.2.00.0000")


def test_datajud_config_requires_explicit_authorized_use(monkeypatch) -> None:
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_API_KEY", "public-key")
    monkeypatch.delenv("DATAJUD_AUTHORIZED_USE", raising=False)

    with pytest.raises(RuntimeError, match="DATAJUD_AUTHORIZED_USE=true"):
        datajud_config()


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf"])
def test_datajud_config_rejects_non_finite_timeout(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DATAJUD_TIMEOUT_SECONDS", value)

    with pytest.raises(RuntimeError, match="finite number greater than zero"):
        datajud_config()


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]


@pytest.mark.asyncio
async def test_datajud_lookup_parses_official_metadata_without_network(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["authorization"] = request.headers.get("Authorization")
        observed["body"] = json.loads(request.data.decode("utf-8"))
        observed["timeout"] = timeout
        return _Response(
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "numeroProcesso": "00000000020268210001",
                                "classe": {"codigo": 7, "nome": "Procedimento Comum Cível"},
                                "assuntos": [
                                    {"codigo": 5804, "nome": "Investigação de Paternidade"}
                                ],
                                "orgaoJulgador": {
                                    "codigo": 123,
                                    "nome": "1ª Vara Cível",
                                    "codigoMunicipioIBGE": 4314902,
                                },
                                "comarca": "Porto Alegre",
                            }
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="rotating-public-key",
            timeout_seconds=3.5,
        ),
    )

    assert result.status == "ok"
    assert result.metadata is not None
    assert result.metadata.class_name == "Procedimento Comum Cível"
    assert result.metadata.class_code == "7"
    assert result.metadata.subjects == (
        {"code": "5804", "name": "Investigação de Paternidade"},
    )
    assert result.metadata.adjudicating_body == "1ª Vara Cível"
    assert result.metadata.county == "Porto Alegre"
    assert observed["url"] == (
        "https://api-publica.datajud.cnj.jus.br/api_publica_tjrs/_search"
    )
    assert observed["authorization"] == "APIKey rotating-public-key"
    assert observed["body"] == {
        "size": 1,
        "query": {"match": {"numeroProcesso": "00000000020268210001"}},
    }
    assert observed["timeout"] == 3.5


@pytest.mark.asyncio
async def test_lookup_matches_official_trf1_query_and_caps_hits_without_network(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"hits": {"hits": []}})

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    result = await lookup_datajud_metadata(
        code="00008323520184013202",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="synthetic-key",
        ),
    )

    assert result.status == "not_found"  # nosec B101 - assert is the pytest check.
    assert observed["url"] == (  # nosec B101 - assert is the pytest check.
        "https://api-publica.datajud.cnj.jus.br/api_publica_trf1/_search"
    )
    assert observed["body"] == {  # nosec B101 - assert is the pytest check.
        "size": 1,
        "query": {
            "match": {"numeroProcesso": "00008323520184013202"},
        },
    }


@pytest.mark.asyncio
async def test_datajud_lookup_is_disabled_and_secret_safe_without_transport(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("transport must not be called")

    monkeypatch.setattr(client, "urlopen", forbidden)

    disabled = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(enabled=False, authorized_use=False, api_key=None),
    )
    secret = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=1,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="public-key",
        ),
    )

    assert disabled.status == "disabled"
    assert secret.status == "skipped_secrecy"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "status"), [(401, "auth_error"), (403, "auth_error"), (429, "unavailable"), (503, "unavailable")])
async def test_datajud_http_failures_are_best_effort(
    monkeypatch,
    status_code: int,
    status: str,
) -> None:
    def failing(request, timeout):
        raise HTTPError(request.full_url, status_code, "failure", {}, None)

    monkeypatch.setattr(client, "urlopen", failing)
    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="rotating-public-key",
        ),
    )

    assert result.status == status
    assert result.error_code == f"http_{status_code}"


@pytest.mark.asyncio
async def test_secret_lookup_skips_before_invalid_runtime_config(monkeypatch) -> None:
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.delenv("DATAJUD_AUTHORIZED_USE", raising=False)
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)

    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=1,
    )

    assert result.status == "skipped_secrecy"


@pytest.mark.asyncio
async def test_datajud_rejects_oversized_response_before_decode(monkeypatch) -> None:
    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, amount: int = -1) -> bytes:
            if amount != client.DATAJUD_MAX_RESPONSE_BYTES + 1:
                raise AssertionError(f"unexpected read bound: {amount}")
            return b"x" * amount

    monkeypatch.setattr(client, "urlopen", lambda request, timeout: OversizedResponse())
    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="synthetic-key",
        ),
    )

    if result.status != "unavailable":
        raise AssertionError(f"unexpected status: {result.status}")
    if result.error_code != "response_too_large":
        raise AssertionError(f"unexpected error code: {result.error_code}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_stage", "expected_exception", "expected_cause"),
    [
        ("opening_response", "URLError", "TimeoutError"),
        ("reading_response_body", "TimeoutError", "none"),
    ],
)
async def test_datajud_transport_error_logs_sanitized_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_stage: str,
    expected_exception: str,
    expected_cause: str,
) -> None:
    sensitive_text = "CNJ=00000000000000000000 Authorization=APIKey secret-value"

    class TimeoutResponse(_Response):
        def read(self, amount: int = -1) -> bytes:
            raise TimeoutError(sensitive_text)

    def failing(request, timeout):
        if failure_stage == "opening_response":
            raise URLError(TimeoutError(sensitive_text))
        return TimeoutResponse({})

    monkeypatch.setattr(client, "urlopen", failing)
    with caplog.at_level(logging.WARNING, logger=client.__name__):
        result = await lookup_datajud_metadata(
            code="0000000-00.2026.8.21.0001",
            secrecy_level=0,
            config=DataJudConfig(
                enabled=True,
                authorized_use=True,
                api_key="secret-value",
                timeout_seconds=3.5,
            ),
        )

    assert result.status == "unavailable"  # nosec B101 - assert is the pytest check.
    assert result.error_code == "transport_error"  # nosec B101 - assert is the pytest check.
    records = [record for record in caplog.records if record.name == client.__name__]
    assert len(records) == 1  # nosec B101 - assert is the pytest check.
    message = records[0].getMessage()
    assert f"stage={failure_stage}" in message  # nosec B101 - assert is the pytest check.
    assert "timeout_seconds=3.500" in message  # nosec B101 - assert is the pytest check.
    assert f"exception_class={expected_exception}" in message  # nosec B101 - assert is the pytest check.
    assert f"cause_class={expected_cause}" in message  # nosec B101 - assert is the pytest check.
    attempt = message.split("attempt_id=", 1)[1].split(" ", 1)[0]
    assert len(attempt) == 32  # nosec B101 - assert is the pytest check.
    int(attempt, 16)
    assert sensitive_text not in message  # nosec B101 - assert is the pytest check.
    assert "secret-value" not in message  # nosec B101 - assert is the pytest check.


class _RawDataJudResponse:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"hits":{"hits":[]},"hits":{"hits":[]}}',
        b'{"hits":{"hits":[{"_source":{"classe":{"codigo":NaN}}}]}}',
    ],
)
@pytest.mark.asyncio
async def test_datajud_rejects_ambiguous_provider_json(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    monkeypatch.setattr(
        client,
        "urlopen",
        lambda request, timeout: _RawDataJudResponse(raw),
    )
    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="synthetic-key",
        ),
    )
    if result.status != "unavailable":
        raise AssertionError(f"unexpected DataJud status: {result.status}")
    if result.error_code != "invalid_json":
        raise AssertionError(f"unexpected DataJud error code: {result.error_code}")
