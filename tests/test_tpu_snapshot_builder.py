import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.tpu_glossary import load_tpu_catalog

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_tpu_snapshot.py"
spec = importlib.util.spec_from_file_location("build_tpu_snapshot", MODULE_PATH)
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

build_snapshot = builder.build_snapshot
load_normalized_csv = builder.load_normalized_csv
write_snapshot = builder.write_snapshot


def _write_csv(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_builder_creates_loader_compatible_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "tpu.csv"
    output = tmp_path / "2026-09-12.json"
    _write_csv(
        source,
        "kind,code,name,definition,source_ref\n"
        "subject,5804,Investigação de Paternidade,Definição assunto,CNJ assunto 5804\n"
        "class,7,Procedimento Comum Cível,Definição classe,CNJ classe 7\n",
    )

    entries = load_normalized_csv(source)
    snapshot = build_snapshot(
        entries=entries,
        tpu_version="2026-09-12",
        source_version_label="12/09/2026",
        input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    write_snapshot(output, snapshot)

    raw = json.loads(output.read_text(encoding="utf-8"))
    assert [entry["kind"] for entry in raw["entries"]] == ["class", "subject"]
    assert raw["build_metadata"]["input_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()

    catalog = load_tpu_catalog(output)
    assert catalog.tpu_version == "2026-09-12"
    assert catalog.resolve(kind="class", code="7") is not None
    assert catalog.resolve(kind="subject", code="5804") is not None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "kind,code,name,definition\nclass,7,Nome,Definição\n",
            "missing required columns",
        ),
        (
            "kind,code,name,definition,source_ref\nother,7,Nome,Definição,ref\n",
            "invalid kind",
        ),
        (
            "kind,code,name,definition,source_ref\nclass,ABC,Nome,Definição,ref\n",
            "digits only",
        ),
        (
            "kind,code,name,definition,source_ref\n"
            "class,7,Nome,Definição,ref\n"
            "class,7,Nome 2,Outra,ref 2\n",
            "duplicate TPU entry",
        ),
    ],
)
def test_builder_rejects_invalid_normalized_input(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    source = tmp_path / "invalid.csv"
    _write_csv(source, body)

    with pytest.raises(ValueError, match=message):
        load_normalized_csv(source)


def test_builder_refuses_to_overwrite_released_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "2026-09-12.json"
    output.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="output already exists"):
        write_snapshot(
            output,
            build_snapshot(
                entries=[],
                tpu_version="2026-09-12",
                source_version_label="12/09/2026",
            ),
        )


@pytest.mark.parametrize("version", ["20260912", "12-09-2026", "2026-9-12", ""])
def test_builder_rejects_noncanonical_version(version: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        build_snapshot(
            entries=[],
            tpu_version=version,
            source_version_label="12/09/2026",
        )
