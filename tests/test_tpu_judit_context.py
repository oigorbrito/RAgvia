from __future__ import annotations

from app.judit import extract_promotable_fields


def test_public_judit_fields_preserve_tpu_codes_without_derived_definitions() -> None:
    process = {
        "code": "0000000-00.2026.8.21.1380",
        "secrecy_level": 0,
        "classifications": [{"code": "7", "name": "NOME RECEBIDO DA JUDIT"}],
        "subjects": [{"code": "5804", "name": "ASSUNTO RECEBIDO DA JUDIT"}],
        "steps": [],
    }

    fields = extract_promotable_fields(process)

    assert fields["class_name"] == "NOME RECEBIDO DA JUDIT"
    assert fields["subjects"] == [
        {"code": "5804", "name": "ASSUNTO RECEBIDO DA JUDIT"}
    ]
    assert fields["header"]["class_code"] == "7"
    assert "tpu_glossary" not in fields["header"]


def test_unknown_tpu_codes_remain_source_fields_without_invented_definitions() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.2026.8.21.1381",
            "secrecy_level": 0,
            "classifications": [{"code": "999999", "name": "Classe recebida"}],
            "subjects": [{"code": "888888", "name": "Assunto recebido"}],
            "steps": [],
        }
    )

    assert fields["header"]["class_code"] == "999999"
    assert "tpu_glossary" not in fields["header"]
    assert fields["subjects"] == [{"code": "888888", "name": "Assunto recebido"}]


def test_secret_process_does_not_promote_tpu_codes_or_subjects() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.2026.8.21.1382",
            "secrecy_level": 1,
            "classifications": [{"code": "7", "name": "Classe restrita"}],
            "subjects": [{"code": "5804", "name": "Assunto restrito"}],
            "steps": [],
        }
    )

    assert fields["subjects"] == []
    assert "class_code" not in fields["header"]
    assert "tpu_glossary" not in fields["header"]
