from __future__ import annotations

from app.judit import extract_promotable_fields


def test_normalizes_step_text_redacts_ids_and_preserves_chunk_metadata() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.2026.8.21.0111",
            "tribunal_acronym": "TJRS",
            "instance": 2,
            "secrecy_level": 0,
            "parties": [
                {
                    "name": "PARTE TESTE",
                    "side": "Active",
                    "person_type": "Natural",
                    "main_document": "12345678901",
                }
            ],
            "steps": [
                {
                    "step_id": "step-27",
                    "step_date": "2026-01-10T02:30:00Z",
                    "step_type": "SENTENCA",
                    "content": "27 - SentençaProferida   para CPF 12345678901\n após audiência",
                    "private": False,
                    "tags": {"source": "synthetic"},
                }
            ],
        }
    )

    assert fields["parties"] == [
        {
            "name": "PARTE TESTE",
            "side": "Active",
            "person_type": "Natural",
            "masked_person_id": "***.***.***-01",
        }
    ]
    assert "12345678901" not in str(fields["parties"])
    step = fields["steps"][0]
    assert step["step_number"] == 1
    assert step["text"] == "Sentença Proferida para CPF [documento removido] após audiência"
    assert "12345678901" not in step["text"]
    assert step["occurred_at"].isoformat() == "2026-01-09T23:30:00-03:00"

    metadata = step["metadata"]
    assert metadata["cnj"] == "0000000-00.2026.8.21.0111"
    assert metadata["instance"] == 2
    assert metadata["court"] == "TJRS"
    assert metadata["type"] == "SENTENCA"
    assert metadata["step_id"] == "step-27"
    assert metadata["step_number"] == 1
    assert metadata["private"] is False
    assert metadata["secrecy_level"] == 0
    assert metadata["source_step_date"] == "2026-01-10T02:30:00Z"
    assert metadata["occurred_at_sao_paulo"] == "2026-01-09T23:30:00-03:00"


def test_naive_source_datetime_is_treated_as_utc_before_sao_paulo_conversion() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.2026.8.21.0112",
            "steps": [
                {
                    "step_date": "2026-06-15T15:00:00",
                    "content": "1. Movimento  com   espaços",
                }
            ],
        }
    )

    step = fields["steps"][0]
    assert step["text"] == "Movimento com espaços"
    assert step["occurred_at"].isoformat() == "2026-06-15T12:00:00-03:00"


def test_public_header_retains_source_phase_status_and_court_unit() -> None:
    fields = extract_promotable_fields(
        {
            "secrecy_level": 0,
            "area": "Cível",
            "justice_description": "Justiça Estadual",
            "phase": "INICIAL",
            "status": "ATIVO",
            "distribution_date": "2026-01-10T00:00:00Z",
            "judge": "Magistrado de teste",
            "courts": [{"name": "Vara de teste"}],
        }
    )

    assert fields["header"] == {  # nosec B101 - pytest assertion
        "area": "Cível",
        "justice_description": "Justiça Estadual",
        "phase": "INICIAL",
        "status": "ATIVO",
        "distribution_date": "2026-01-10T00:00:00Z",
        "judge": "Magistrado de teste",
        "court_unit": "Vara de teste",
    }


def test_public_source_header_bounds_values_and_masks_personal_ids() -> None:
    fields = extract_promotable_fields(
        {
            "secrecy_level": 0,
            "status": {"name": "ATIVO CPF 12345678901"},
            "phase": {"unexpected": "must omit"},
            "courts": [{"name": "Vara CPF 123.456.789-01"}],
        }
    )

    assert fields["header"]["status"] == "ATIVO CPF [documento removido]"  # nosec B101 - pytest assertion
    assert fields["header"]["court_unit"] == "Vara CPF [documento removido]"  # nosec B101 - pytest assertion
    assert "phase" not in fields["header"]  # nosec B101 - pytest assertion
    assert "12345678901" not in str(fields["header"])  # nosec B101 - pytest assertion


def test_secret_header_does_not_expand_to_status_or_phase() -> None:
    fields = extract_promotable_fields(
        {
            "secrecy_level": 1,
            "phase": "RESTRITA",
            "status": "ATIVO",
            "judge": "Magistrado de teste",
            "parties": [{"name": "Não deve aparecer"}],
        }
    )

    assert fields["header"] == {}  # nosec B101 - pytest assertion
    assert fields["parties"] == []  # nosec B101 - pytest assertion


def test_normalized_person_type_handles_unhashable_input_types() -> None:
    from app.judit import _normalized_person_type

    assert _normalized_person_type({"type": "ADVOGADO"}) == "TYPE_ADVOGADO"
    assert _normalized_person_type(["ADVOGADO", "AUTOR"]) == "ADVOGADO_AUTOR"
    assert _normalized_person_type(None) == ""
