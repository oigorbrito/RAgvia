from copy import deepcopy

from app.datajud_enrichment import DataJudMetadata, merge_datajud_metadata


def _judit_process() -> dict:
    return {
        "header": {
            "class_code": "OLD-7",
            "county": "Porto Alegre",
            "amount": "1000.00",
        },
        "parties": [{"name": "Parte A", "side": "active"}],
        "subjects": [{"code": "5804", "name": "Assunto Judit"}],
        "steps": [{"step_number": 1, "text": "Movimento preservado"}],
        "court": "TJRS",
        "class_name": "Classe Judit",
        "secrecy_level": 0,
    }


def test_datajud_merge_uses_official_metadata_and_preserves_judit_owned_content() -> None:
    original = _judit_process()
    before = deepcopy(original)
    result = merge_datajud_metadata(
        original,
        DataJudMetadata(
            class_name="Procedimento Comum Cível",
            class_code="7",
            subjects=(
                {"code": "5804", "name": "Investigação de Paternidade"},
                {"code": "9999", "name": "Outro assunto oficial"},
            ),
            adjudicating_body="1ª Vara Cível",
            county="Porto Alegre",
            source_ref="DataJud fixture v1",
        ),
    )

    assert original == before
    assert result.datajud_applied is True
    assert result.process["class_name"] == "Procedimento Comum Cível"
    assert result.process["header"]["class_code"] == "7"
    assert result.process["header"]["adjudicating_body"] == "1ª Vara Cível"
    assert result.process["header"]["county"] == "Porto Alegre"
    assert result.process["header"]["amount"] == "1000.00"
    assert result.process["subjects"] == [
        {"code": "5804", "name": "Investigação de Paternidade"},
        {"code": "9999", "name": "Outro assunto oficial"},
    ]
    assert result.process["parties"] == before["parties"]
    assert result.process["steps"] == before["steps"]
    assert all(item.source_ref == "DataJud fixture v1" for item in result.provenance if item.selected_source == "datajud")


def test_datajud_merge_records_conflicts_without_silent_overwrite() -> None:
    result = merge_datajud_metadata(
        _judit_process(),
        DataJudMetadata(
            class_name="Classe Oficial",
            class_code="7",
            subjects=({"code": "111", "name": "Assunto Oficial"},),
            adjudicating_body="Órgão oficial",
            county="Canoas",
            source_ref="DataJud fixture conflict",
        ),
    )

    conflicts = {item.field for item in result.provenance if item.conflict}
    assert conflicts == {"class_name", "class_code", "subjects", "county"}
    assert len(result.warnings) == 4
    assert all("DataJud" in warning for warning in result.warnings)


def test_datajud_missing_fields_fall_back_to_judit_without_conflict() -> None:
    result = merge_datajud_metadata(
        _judit_process(),
        DataJudMetadata(source_ref="DataJud empty fixture"),
    )

    assert result.process["class_name"] == "Classe Judit"
    assert result.process["header"]["class_code"] == "OLD-7"
    assert result.process["header"]["county"] == "Porto Alegre"
    assert result.process["subjects"] == [{"code": "5804", "name": "Assunto Judit"}]
    assert result.warnings == ()
    selected = {item.field: item.selected_source for item in result.provenance}
    assert selected == {
        "class_name": "judit",
        "class_code": "judit",
        "adjudicating_body": "judit",
        "county": "judit",
        "subjects": "judit",
    }


def test_datajud_is_not_applied_to_secret_process() -> None:
    process = _judit_process()
    process["secrecy_level"] = 1

    result = merge_datajud_metadata(
        process,
        DataJudMetadata(
            class_name="Classe Oficial",
            subjects=({"code": "111", "name": "Assunto Oficial"},),
        ),
    )

    assert result.datajud_applied is False
    assert result.process == process
    assert result.provenance == ()
    assert result.warnings == ()


def test_subjects_are_deduplicated_deterministically() -> None:
    result = merge_datajud_metadata(
        _judit_process(),
        DataJudMetadata(
            subjects=(
                {"code": "5804", "name": "Investigação"},
                {"code": "5804", "name": "Investigação"},
            )
        ),
    )

    assert result.process["subjects"] == [{"code": "5804", "name": "Investigação"}]
