import copy
import json
from pathlib import Path

import pytest

from app.judit import extract_promotable_fields, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "judit"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_parse_event_rejects_non_finite_numbers_before_persistence(value: float) -> None:
    body = {
        "callback_id": "cb-non-finite",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": "req-1",
        "payload": {
            "request_id": "req-1",
            "response_id": "resp-1",
            "response_type": "lawsuit",
            "response_data": {
                "code": "0000000-00.0000.0.00.0000",
                "steps": [{"metadata": {"score": value}}],
            },
        },
    }

    with pytest.raises(ValueError, match="non-finite number"):
        parse_event(body)


def test_parse_event_handles_deep_nesting_without_recursion_error() -> None:
    nested: dict[str, object] = {"value": 1.0}
    for _ in range(1500):
        nested = {"nested": nested}

    event = parse_event(
        {
            "callback_id": "cb-deep",
            "event_type": "request_completed",
            "reference_type": "request",
            "reference_id": "req-deep",
            "payload": {"status": "completed"},
            "unused": nested,
        }
    )

    if event.request_id != "req-deep" or event.request_completed is not True:
        raise AssertionError("deep finite webhook payload was not parsed correctly")


def test_parse_current_lawsuit_response_envelope() -> None:
    event = parse_event(
        {
            "callback_id": "cb-1",
            "event_type": "response_created",
            "reference_type": "request",
            "reference_id": "req-1",
            "payload": {
                "request_id": "req-1",
                "response_id": "resp-1",
                "response_type": "lawsuit",
                "response_data": {
                    "code": "0000000-00.0000.0.00.0000",
                    "steps": [],
                },
                "tags": {"cached_response": False},
            },
        }
    )
    assert event.is_lawsuit_response is True
    assert event.request_completed is False
    assert event.cached_response is False
    assert event.request_id == "req-1"
    assert event.response_id == "resp-1"
    assert event.code == "0000000-00.0000.0.00.0000"


def test_request_completed_does_not_require_process_code() -> None:
    event = parse_event(
        {
            "callback_id": "cb-done",
            "event_type": "request_completed",
            "reference_type": "request",
            "reference_id": "req-1",
            "payload": {"status": "completed"},
        }
    )
    assert event.request_completed is True
    assert event.code is None
    assert event.request_id == "req-1"


def test_tracking_lawsuit_prefers_payload_request_id_over_tracking_reference() -> None:
    event = parse_event(_fixture("tracking_lawsuit_response.json"))

    assert event.is_lawsuit_response is True
    assert event.request_id == "request-abc"
    assert event.request_id != "tracking-123"
    assert event.response_id == "response-lawsuit-001"
    assert event.cached_response is False
    assert event.code == "0000000-00.2026.8.21.0001"


def test_application_info_600_is_request_completion() -> None:
    event = parse_event(_fixture("tracking_request_completed.json"))

    assert event.is_lawsuit_response is False
    assert event.request_completed is True
    assert event.request_id == "request-abc"
    assert event.response_type == "application_info"
    assert event.code == "600"


def test_application_info_without_completion_marker_is_not_complete() -> None:
    payload = _fixture("tracking_request_completed.json")
    payload["payload"]["response_data"] = {"code": 601, "message": "OTHER_INFO"}

    event = parse_event(payload)

    assert event.request_completed is False


def test_extract_current_judit_steps_and_sanitizes_parties() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "tribunal_acronym": "TJRS",
            "secrecy_level": 0,
            "classifications": [{"code": "7", "name": "PROCEDIMENTO COMUM CÍVEL"}],
            "subjects": [{"code": "1", "name": "DIREITO CIVIL"}],
            "parties": [
                {
                    "name": "Parte A",
                    "side": "Active",
                    "person_type": "Autor",
                    "main_document": "12345678901",
                    "documents": [{"document": "12345678901", "document_type": "cpf"}],
                }
            ],
            "steps": [
                {
                    "step_id": "s1",
                    "step_date": "2026-01-01T00:00:00Z",
                    "content": "CITAÇÃO expedida",
                    "private": False,
                },
                {
                    "step_id": "s2",
                    "step_date": "2026-02-01T00:00:00Z",
                    "step_type": "SENTENCA",
                    "content": "SENTENÇA proferida",
                    "private": False,
                },
            ],
        }
    )
    assert fields["court"] == "TJRS"
    assert fields["class_name"] == "PROCEDIMENTO COMUM CÍVEL"
    assert len(fields["steps"]) == 2
    assert fields["steps"][0]["step_number"] == 1
    assert fields["steps"][0]["metadata"]["source_step_number"] is None
    assert "SENTENÇA" in fields["steps"][1]["text"]
    assert fields["parties"] == [
        {
            "name": "Parte A",
            "side": "Active",
            "person_type": "Autor",
            "masked_person_id": "***.***.***-01",
        }
    ]
    assert "12345678901" not in str(fields["parties"])


@pytest.mark.parametrize(
    "representative_type",
    [
        "ADVOGADO",
        "advogada",
        "PROCURADOR",
        "representante",
        "LEGAL REPRESENTATIVE",
    ],
)
def test_separates_representatives_from_process_parties(representative_type: str) -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.2026.8.21.0001",
            "parties": [
                {
                    "name": "Pessoa Autora Exemplo",
                    "side": "Active",
                    "person_type": "PERSON",
                },
                {
                    "name": "Representante Exemplo",
                    "side": "Active",
                    "person_type": representative_type,
                    "represented_party_name": "Pessoa Autora Exemplo",
                    "main_document": "12345678901",
                },
                {
                    "name": "Empresa Ré Exemplo",
                    "side": "Passive",
                    "person_type": "COMPANY",
                },
            ],
            "steps": [],
        }
    )

    assert [party["name"] for party in fields["parties"]] == [
        "Pessoa Autora Exemplo",
        "Empresa Ré Exemplo",
    ]
    assert fields["representatives"] == [
        {
            "name": "Representante Exemplo",
            "side": "Active",
            "person_type": representative_type,
            "masked_person_id": "***.***.***-01",
            "represents": "Pessoa Autora Exemplo",
        }
    ]
    assert "Representante Exemplo" not in {
        party["name"] for party in fields["parties"]
    }
    assert "12345678901" not in json.dumps(fields, ensure_ascii=False, default=str)


def test_representative_without_explicit_relation_is_not_attached_to_a_party() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.2026.8.21.0001",
            "parties": [
                {
                    "name": "Pessoa Autora Exemplo",
                    "side": "Active",
                    "person_type": "PERSON",
                },
                {
                    "name": "Advogado Exemplo",
                    "side": "Active",
                    "person_type": "ADVOGADO",
                },
            ],
            "steps": [],
        }
    )

    assert [party["name"] for party in fields["parties"]] == ["Pessoa Autora Exemplo"]
    assert fields["representatives"] == [
        {
            "name": "Advogado Exemplo",
            "side": "Active",
            "person_type": "ADVOGADO",
        }
    ]
    assert "represents" not in fields["representatives"][0]


def test_normalizes_attachment_manifest_without_copying_arbitrary_fields() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "instance": 1,
            "attachments": [
                {
                    "attachment_id": "att-1",
                    "attachment_date": "2026-09-17T12:00:00Z",
                    "attachment_name": "DECISAO.pdf",
                    "status": "DONE",
                    "signed_url": "https://sensitive.example.invalid/document",
                    "secret": "must-not-be-copied",
                },
                {
                    "attachment_id": "att-1",
                    "attachment_name": "duplicate.pdf",
                },
                {"status": "done"},
            ],
            "steps": [],
        }
    )

    assert fields["attachments"] == [
        {
            "attachment_id": "att-1",
            "attachment_date": fields["attachments"][0]["attachment_date"],
            "attachment_name": "DECISAO.pdf",
            "provider_status": "done",
        }
    ]
    assert fields["attachments"][0]["attachment_date"].isoformat() == "2026-09-17T09:00:00-03:00"
    assert "signed_url" not in fields["attachments"][0]
    assert "secret" not in fields["attachments"][0]


def test_secret_process_discards_attachment_manifest() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "secrecy_level": 1,
            "attachments": [{"attachment_id": "att-secret", "status": "done"}],
            "steps": [],
        }
    )

    assert fields["attachments"] == []


def test_masks_cpf_and_cnpj_without_mutating_raw_process() -> None:
    process = {
        "code": "0000000-00.0000.0.00.0000",
        "parties": [
            {
                "name": "Pessoa Física",
                "side": "Active",
                "person_type": "Autor",
                "main_document": "123.456.789-01",
            },
            {
                "name": "Pessoa Jurídica",
                "side": "Passive",
                "person_type": "Réu",
                "documents": [
                    {"document": "12.345.678/0001-95", "document_type": "cnpj"}
                ],
            },
        ],
        "steps": [],
    }
    original = copy.deepcopy(process)

    fields = extract_promotable_fields(process)

    assert process == original
    assert fields["parties"][0]["masked_person_id"] == "***.***.***-01"
    assert fields["parties"][1]["masked_person_id"] == "**.***.***/****-95"
    rendered = json.dumps(fields, ensure_ascii=False, default=str)
    assert "12345678901" not in rendered
    assert "123.456.789-01" not in rendered
    assert "12345678000195" not in rendered
    assert "12.345.678/0001-95" not in rendered


def test_corrects_eletronica_refer_only_in_derived_step_text() -> None:
    process = {
        "code": "0000000-00.0000.0.00.0000",
        "steps": [
            {
                "step_number": 8,
                "content": "8 - INTIMAÇÃO ELETRÔNICAREFER registrada",
            }
        ],
    }
    original = copy.deepcopy(process)

    fields = extract_promotable_fields(process)

    assert process == original
    assert process["steps"][0]["content"] == "8 - INTIMAÇÃO ELETRÔNICAREFER registrada"
    assert fields["steps"][0]["text"] == "INTIMAÇÃO ELETRÔNICA REFER registrada"
    assert fields["steps"][0]["metadata"]["source_step_number"] == 8


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("2026-06-10T12:00:00Z", "2026-06-10T09:00:00-03:00"),
        ("2026-06-10T12:00:00+00:00", "2026-06-10T09:00:00-03:00"),
        ("2026-06-10T09:00:00-03:00", "2026-06-10T09:00:00-03:00"),
        ("2026-06-10T12:00:00", "2026-06-10T09:00:00-03:00"),
        ("not-a-date", None),
    ],
)
def test_normalizes_step_dates_for_sao_paulo(source: str, expected: str | None) -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "steps": [{"step_date": source, "content": "Movimento"}],
        }
    )

    assert fields["steps"][0]["metadata"]["source_step_date"] == source
    assert fields["steps"][0]["metadata"]["occurred_at_sao_paulo"] == expected
    if expected is None:
        assert fields["steps"][0]["occurred_at"] is None
    else:
        assert fields["steps"][0]["occurred_at"].isoformat() == expected


def test_preserves_explicit_source_step_numbers_without_changing_internal_order() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "steps": [
                {"step_number": 7, "content": "Primeiro movimento recebido"},
                {"event_number": "9", "content": "Segundo movimento recebido"},
                {"movement_number": 12, "content": "Terceiro movimento recebido"},
                {"sequence_number": "15", "content": "Quarto movimento recebido"},
            ],
        }
    )

    assert [step["step_number"] for step in fields["steps"]] == [1, 2, 3, 4]
    assert [
        step["metadata"]["source_step_number"] for step in fields["steps"]
    ] == [7, 9, 12, 15]


def test_does_not_infer_source_step_number_from_step_id_or_invalid_values() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "steps": [
                {"step_id": "123", "content": "Sem número explícito"},
                {"step_number": "evento-8", "content": "Número não estritamente inteiro"},
                {"event_number": -1, "content": "Número negativo"},
                {"movement_number": True, "content": "Booleano não é sequência"},
            ],
        }
    )

    assert [
        step["metadata"]["source_step_number"] for step in fields["steps"]
    ] == [None, None, None, None]


def test_extracts_promotable_fields_from_tracking_fixture_without_documents() -> None:
    event = parse_event(_fixture("tracking_lawsuit_response.json"))
    fields = extract_promotable_fields(event.response_data or {})

    assert fields["court"] == "TJRS"
    assert fields["class_name"] == "PROCEDIMENTO COMUM CÍVEL"
    assert fields["header"]["state"] == "RS"
    assert fields["header"]["city"] == "Porto Alegre"
    assert len(fields["steps"]) == 2
    assert fields["parties"][0] == {
        "name": "PARTE AUTORA TESTE",
        "side": "Active",
        "person_type": "Autor",
        "masked_person_id": "***.***.***-00",
    }
    assert "00000000000" not in str(fields["parties"])
