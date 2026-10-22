from uuid import UUID

from app.rag import (
    PASSIVE_PARTY_NO_REPRESENTATIVE_OR_CITATION_WARNING,
    _passive_party_attention_warnings,
)
from app.retrieval import Step


def _step(title: str, text: str = "") -> Step:
    return Step(
        id=UUID(int=1),
        step_number=1,
        title=title,
        text=text,
    )


def _expect_warnings(actual: list[str], expected: list[str]) -> None:
    if actual != expected:
        raise AssertionError(f"unexpected warnings: {actual!r}; expected {expected!r}")


def test_flags_passive_party_when_no_representatives_or_citation_exist() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [],
        [_step("Distribuição"), _step("Juntada de petição")],
    )

    _expect_warnings(
        warnings,
        [PASSIVE_PARTY_NO_REPRESENTATIVE_OR_CITATION_WARNING],
    )


def test_does_not_flag_when_explicit_citation_exists() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [],
        [_step("CITAÇÃO", "Citação registrada nos autos.")],
    )

    _expect_warnings(warnings, [])


def test_does_not_flag_when_any_normalized_representative_exists() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [{"name": "Representante Sintético", "person_type": "LAWYER"}],
        [_step("Distribuição")],
    )

    _expect_warnings(warnings, [])


def test_does_not_infer_passive_party_from_person_type_or_name() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Empresa Ré Sintética", "person_type": "Réu"}],
        [],
        [_step("Distribuição")],
    )

    _expect_warnings(warnings, [])


def test_accepts_unaccented_explicit_citation_marker() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [],
        [_step("Citacao eletrônica realizada")],
    )

    _expect_warnings(warnings, [])
