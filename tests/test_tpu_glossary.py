from __future__ import annotations

import json

import pytest

from app.tpu_glossary import load_tpu_catalog, resolve_process_tpu_context


def test_known_codes_resolve_from_pinned_version_without_replacing_source_fields(tmp_path) -> None:
    path = tmp_path / "tpu.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tpu_version": "synthetic-v1",
                "publisher": "CNJ sintético",
                "source": "TPU sintética",
                "source_version_label": "synthetic-2026-09-17",
                "entries": [
                    {
                        "kind": "class",
                        "code": "7",
                        "name": "Classe Sintética",
                        "definition": "Definição sintética da classe.",
                        "source_ref": "fixture class 7",
                    },
                    {
                        "kind": "subject",
                        "code": "5804",
                        "name": "Assunto Sintético",
                        "definition": "Definição sintética do assunto.",
                        "source_ref": "fixture subject 5804",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog = load_tpu_catalog(path)
    subjects = [
        {"code": "5804", "name": "Nome recebido da fonte"},
        {"code": "5804", "name": "Duplicado"},
    ]

    resolved = resolve_process_tpu_context(
        class_code="7",
        subjects=subjects,
        catalog=catalog,
    )

    assert subjects[0]["name"] == "Nome recebido da fonte"
    assert [item["kind"] for item in resolved] == ["class", "subject"]
    assert [item["code"] for item in resolved] == ["7", "5804"]
    assert {item["tpu_version"] for item in resolved} == {"synthetic-v1"}
    assert resolved[0]["definition"] == "Definição sintética da classe."
    assert resolved[1]["definition"] == "Definição sintética do assunto."


def test_unknown_and_malformed_codes_are_omitted_without_name_fallback(tmp_path) -> None:
    path = tmp_path / "tpu.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tpu_version": "synthetic-v2",
                "publisher": "CNJ sintético",
                "source": "TPU sintética",
                "source_version_label": "synthetic-2026-09-17",
                "entries": [
                    {
                        "kind": "subject",
                        "code": "100",
                        "name": "Nome Igual",
                        "definition": "Definição conhecida.",
                        "source_ref": "fixture subject 100",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog = load_tpu_catalog(path)

    resolved = resolve_process_tpu_context(
        class_code="999",
        subjects=[
            {"code": "999", "name": "Nome Igual"},
            {"name": "Nome Igual"},
            "Nome Igual",
        ],
        catalog=catalog,
    )

    assert resolved == []


def test_catalog_version_and_entries_are_selected_by_snapshot(tmp_path) -> None:
    def write_snapshot(name: str, version: str, definition: str):
        path = tmp_path / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tpu_version": version,
                    "publisher": "CNJ sintético",
                    "source": "TPU sintética",
                    "source_version_label": version,
                    "entries": [
                        {
                            "kind": "class",
                            "code": "7",
                            "name": "Classe",
                            "definition": definition,
                            "source_ref": f"fixture {version}",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    older = load_tpu_catalog(write_snapshot("old.json", "v1", "Definição antiga."))
    newer = load_tpu_catalog(write_snapshot("new.json", "v2", "Definição nova."))

    assert older.resolve(kind="class", code="7").tpu_version == "v1"  # type: ignore[union-attr]
    assert newer.resolve(kind="class", code="7").tpu_version == "v2"  # type: ignore[union-attr]
    assert older.resolve(kind="class", code="7").definition == "Definição antiga."  # type: ignore[union-attr]
    assert newer.resolve(kind="class", code="7").definition == "Definição nova."  # type: ignore[union-attr]


def test_invalid_snapshot_fails_closed(tmp_path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "tpu_version": "invalid",
                "publisher": "CNJ sintético",
                "source": "TPU sintética",
                "source_version_label": "invalid",
                "entries": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="schema_version"):
        load_tpu_catalog(path)
