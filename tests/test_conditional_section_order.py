from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT
from app.validation import validar

CODE = "0000000-00.0000.0.00.0000"


def _validate(text: str):
    return validar(text=text, code=CODE, parties=[])


def test_prompt_documents_conditional_section_order() -> None:
    positions = [
        PROCESS_SUMMARY_SYSTEM_PROMPT.index(field)
        for field in (
            "decisions",
            "deadlines",
            "related_processes",
            "attachments",
        )
    ]
    assert positions == sorted(positions)
    assert "são condicionais" in PROCESS_SUMMARY_SYSTEM_PROMPT


def test_accepts_conditional_sections_in_documented_relative_order() -> None:
    result = _validate(
        "# Resumo do processo\n\n"
        "## Decisões\nDecisão registrada.\n\n"
        "## Processos relacionados\nProcesso relacionado registrado.\n\n"
        "## Anexos\nAnexo registrado."
    )
    assert result.passed is True


def test_accepts_omitted_conditional_sections() -> None:
    result = _validate(
        "# Resumo do processo\n\n"
        "## Síntese\nConteúdo factual.\n\n"
        "## Anexos\nAnexo registrado."
    )
    assert result.passed is True


def test_rejects_conditional_sections_out_of_order() -> None:
    result = _validate(
        "# Resumo do processo\n\n"
        "## Anexos\nAnexo registrado.\n\n"
        "## Decisões\nDecisão registrada."
    )
    assert result.passed is False
    assert any("conditional sections must follow order" in error for error in result.errors)


def test_plain_mentions_do_not_trigger_heading_order_validation() -> None:
    result = _validate(
        "## Síntese\nO texto menciona Anexos antes de Decisões, sem criar as seções."
    )
    assert result.passed is True
