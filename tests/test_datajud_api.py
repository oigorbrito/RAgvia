from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import app
from app.datajud_client import datajud_activation_status
from app.datajud_jobs import metadata_response

CNJ = "0000000-00.2024.8.26.0100"


def test_metadata_response_serializes_only_modeled_metadata() -> None:
    assert metadata_response(  # nosec B101
        CNJ,
        {
            "class_name": "Procedimento de teste",
            "class_code": "123",
            "subjects": [{"code": "456", "name": "Assunto de teste"}],
            "adjudicating_body": "Vara de teste",
            "county": "Comarca de teste",
            "parties": [{"name": "unmodeled"}],
            "movements": [{"name": "unmodeled"}],
        },
    ) == {
        "code": CNJ,
        "source": "datajud",
        "court": "TJSP",
        "class_name": "Procedimento de teste",
        "adjudicating_body": "Vara de teste",
        "county": "Comarca de teste",
        "subjects": [{"code": "456", "name": "Assunto de teste"}],
        "parties": [],
    }


def test_api_activation_uses_authorization_flags_without_api_key(monkeypatch) -> None:
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    assert datajud_activation_status() == (True, None)  # nosec B101


def test_legacy_get_is_rejected_even_when_datajud_is_activated(monkeypatch) -> None:
    token = f"datajud-{uuid4()}"
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    monkeypatch.setattr(app.state, "bearer_tokens", {token: uuid4()}, raising=False)
    client = TestClient(app)
    for path in (f"/processes/{CNJ}/datajud", f"/processos/{CNJ}/datajud", f"/v1/processos/{CNJ}/datajud"):
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 405  # nosec B101
        assert response.headers["allow"] == "POST"  # nosec B101


def test_datajud_submission_requires_explicit_activation_and_never_looks_up_inline(monkeypatch) -> None:
    token = f"datajud-{uuid4()}"
    monkeypatch.setenv("DATAJUD_ENABLED", "false")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "false")
    monkeypatch.setattr(app.state, "bearer_tokens", {token: uuid4()}, raising=False)
    response = TestClient(app).post(
        "/processes/datajud/jobs",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "test-request-1"},
        json={"code": CNJ},
    )
    assert response.status_code == 403  # nosec B101
    assert response.json()["detail"] == "DataJud desabilitado por padrão no servidor (DATAJUD_ENABLED=false)."  # nosec B101
