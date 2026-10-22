from datetime import datetime, timezone

from app.process_semantics import SEMANTIC_SCHEMA_VERSION, semantic_fingerprint


def _fields() -> dict:
    return {
        "header": {"instance": 1, "state": "RS"},
        "parties": [{"name": "Parte Sintética", "masked_person_id": "***.***.***-44"}],
        "subjects": [{"code": "1", "name": "Obrigação"}],
        "steps": [
            {
                "step_number": 1,
                "occurred_at": datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
                "title": "CITAÇÃO",
                "text": "Citação realizada",
                "metadata": {
                    "source_step_number": 10,
                    "private": False,
                    "secrecy_level": 0,
                    "tags": {"kind": "movement"},
                    "source_step_date": "source-format-not-semantic",
                },
            }
        ],
        "attachments": [],
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "secrecy_level": 0,
        "representatives": [],
    }


def test_semantic_fingerprint_is_stable_for_equivalent_normalized_data() -> None:
    left = _fields()
    right = _fields()
    right["header"] = {"state": "RS", "instance": 1}
    right["steps"][0]["metadata"]["source_step_date"] = "different-envelope-format"

    assert semantic_fingerprint(**left) == semantic_fingerprint(**right)


def test_semantic_fingerprint_changes_for_new_movement_content() -> None:
    left = _fields()
    right = _fields()
    right["steps"][0]["text"] = "Sentença proferida"

    assert semantic_fingerprint(**left) != semantic_fingerprint(**right)


def test_semantic_fingerprint_changes_for_relevant_structured_metadata() -> None:
    left = _fields()
    right = _fields()
    right["class_name"] = "Execução Fiscal"

    assert semantic_fingerprint(**left) != semantic_fingerprint(**right)


def test_semantic_schema_version_tracks_attachment_manifest_contract() -> None:
    assert SEMANTIC_SCHEMA_VERSION == 3


def test_semantic_fingerprint_changes_when_attachment_manifest_changes() -> None:
    left = _fields()
    right = _fields()
    right["attachments"] = [
        {
            "attachment_id": "att-1",
            "attachment_date": datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
            "attachment_name": "DECISAO.pdf",
            "provider_status": "done",
        }
    ]

    assert semantic_fingerprint(**left) != semantic_fingerprint(**right)


def test_local_attachment_processing_status_is_not_semantic() -> None:
    left = _fields()
    right = _fields()
    attachment = {
        "attachment_id": "att-1",
        "attachment_date": datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        "attachment_name": "DECISAO.pdf",
        "provider_status": "done",
    }
    left["attachments"] = [dict(attachment, status="pending")]
    right["attachments"] = [dict(attachment, status="ready")]

    assert semantic_fingerprint(**left) == semantic_fingerprint(**right)


def test_semantic_fingerprint_changes_when_representatives_change() -> None:
    left = _fields()
    right = _fields()
    right["representatives"] = [
        {
            "name": "Advogado Exemplo",
            "side": "Active",
            "person_type": "ADVOGADO",
            "represents": "Parte Sintética",
        }
    ]

    assert semantic_fingerprint(**left) != semantic_fingerprint(**right)
