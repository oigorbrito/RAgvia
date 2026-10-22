import pytest

from app.validation import validar

CODE = "0000000-00.0000.0.00.0000"
PARTIES = [{"name": "Maria da Silva"}]
STEPS = [
    {"step_number": 1, "occurred_at": "2026-09-14T12:00:00+00:00"},
    {"step_number": 2, "occurred_at": "2026-09-15T13:00:00+00:00"},
    {"step_number": 3, "occurred_at": "2026-09-16T14:00:00+00:00"},
]
SOURCE = (
    '{"header":{"distribution_date":"2026-09-13","area":"Cível",'
    '"county":"Porto Alegre","phase":"Conhecimento",'
    '"judging_body":"2ª Vara Cível"},'
    '"class_name":"Procedimento Comum Cível",'
    '"subjects":["Contratos","Responsabilidade Civil"],'
    '"tags":["urgente"],'
    '"steps":[{"occurred_at":"2026-09-14T12:00:00+00:00"},'
    '{"occurred_at":"2026-09-15T13:00:00+00:00"},'
    '{"occurred_at":"2026-09-16T14:00:00+00:00"}]}'
)


def test_accepts_movement_count_equal_to_context_steps() -> None:
    result = validar(
        text="O processo contém 3 movimentações.",
        code=CODE,
        parties=PARTIES,
        steps=STEPS,
    )
    assert result.passed is True


def test_rejects_movement_count_different_from_context_steps() -> None:
    result = validar(
        text="O processo contém 4 movimentos.",
        code=CODE,
        parties=PARTIES,
        steps=STEPS,
    )
    assert result.passed is False
    assert "movement count mismatch: stated 4, expected 3" in result.errors


def test_accepts_date_present_in_source_context_across_rendering_formats() -> None:
    result = validar(
        text="A distribuição ocorreu em 13/09/2026 e houve ato em 2026-09-15.",
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is True


def test_rejects_date_absent_from_source_context() -> None:
    result = validar(
        text="A audiência ocorreu em 17/09/2026.",
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is False
    assert "date not present in source context: 2026-09-17" in result.errors


def test_accepts_sao_paulo_day_converted_from_utc_timestamp() -> None:
    source = '{"steps":[{"occurred_at":"2026-09-18T01:30:00+00:00"}]}'
    result = validar(
        text="O ato ocorreu em 17/09/2026.",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is True


def test_accepts_literal_utc_day_alongside_local_conversion() -> None:
    source = '{"steps":[{"occurred_at":"2026-09-18T01:30:00Z"}]}'
    result = validar(
        text="A fonte registra 18/09/2026.",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is True


def test_rejects_day_not_literal_or_valid_timezone_conversion() -> None:
    source = '{"steps":[{"occurred_at":"2026-09-18T01:30:00+00:00"}]}'
    result = validar(
        text="O ato ocorreu em 16/09/2026.",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is False
    assert "date not present in source context: 2026-09-16" in result.errors


def test_accepts_source_backed_structured_fields() -> None:
    result = validar(
        text=(
            "- Área: Cível\n"
            "- Classe: Procedimento Comum Cível\n"
            "- Comarca: Porto Alegre\n"
            "- Órgão julgador: 2ª Vara Cível\n"
            "- Fase: Conhecimento\n"
            "- Assuntos: Contratos; Responsabilidade Civil\n"
            "- Tags: urgente"
        ),
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is True


def test_accepts_source_backed_rito_and_gratuidade_claims() -> None:
    source = (
        '{"processo":{"header":{"procedure":"Juizado Especial Cível"}},'
        '"movimentos":[{"text":"Justiça gratuita deferida."}]}'
    )
    result = validar(
        text="- Rito: Juizado Especial Cível\n- Justiça gratuita: deferida",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is True


def test_rejects_gratuidade_when_value_exists_only_outside_movements() -> None:
    source = (
        '{"processo":{"header":{"gratuidade":"deferida"}},'
        '"movimentos":[{"text":"Petição inicial distribuída."}]}'
    )
    result = validar(
        text="- Justiça gratuita: deferida",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is False
    assert "source-backed field mismatch: Justiça gratuita=deferida" in result.errors


def test_rejects_rito_and_gratuidade_values_absent_from_source_context() -> None:
    source = (
        '{"processo":{"header":{"procedure":"Procedimento Comum"}},'
        '"movimentos":[{"text":"Pedido de gratuidade pendente de análise."}]}'
    )
    result = validar(
        text="- Rito: Juizado Especial Cível\n- Justiça gratuita: deferida",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is False
    assert "source-backed field mismatch: Rito=Juizado Especial Cível" in result.errors
    assert "source-backed field mismatch: Justiça gratuita=deferida" in result.errors


def test_rejects_structured_field_value_absent_from_source_context() -> None:
    result = validar(
        text="- Área: Penal\n- Classe: Habeas Corpus",
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is False
    assert "source-backed field mismatch: Área=Penal" in result.errors
    assert "source-backed field mismatch: Classe=Habeas Corpus" in result.errors


def test_rejects_only_missing_item_from_subject_list() -> None:
    result = validar(
        text="Assuntos: Contratos, Direito Tributário",
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is False
    assert "source-backed field mismatch: Assuntos=Direito Tributário" in result.errors
    assert "source-backed field mismatch: Assuntos=Contratos" not in result.errors


def test_does_not_apply_source_backed_claim_check_without_source_context() -> None:
    result = validar(
        text="Área: Penal",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is True


def test_accepts_nonempty_attention_section_when_required() -> None:
    result = validar(
        text="## Pontos de atenção\nNenhuma divergência factual identificada.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
    )
    assert result.passed is True


def test_rejects_missing_attention_section_when_required() -> None:
    result = validar(
        text="## Situação atual\nProcesso em andamento.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
    )
    assert result.passed is False
    assert "Pontos de atenção section is required" in result.errors


def test_rejects_empty_attention_section_when_required() -> None:
    result = validar(
        text="## Pontos de atenção\n\n## Situação atual\nProcesso em andamento.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
    )
    assert result.passed is False
    assert "Pontos de atenção section must not be empty" in result.errors


def test_accepts_required_attention_fact_with_case_and_accent_normalization() -> None:
    fact = "Nenhum movimento processual foi fornecido no payload."
    result = validar(
        text="## Pontos de atenção\nNENHUM MOVIMENTO PROCESSUAL foi fornecido no payload.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
        required_attention_phrases=[fact],
    )
    assert result.passed is True


def test_rejects_missing_required_attention_fact() -> None:
    fact = "Nenhum movimento processual foi fornecido no payload."
    result = validar(
        text="## Pontos de atenção\nNenhuma divergência factual identificada.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
        required_attention_phrases=[fact],
    )
    assert result.passed is False
    assert f"required attention fact missing: {fact}" in result.errors


def test_rejects_required_attention_fact_outside_attention_section() -> None:
    fact = "Nenhum movimento processual foi fornecido no payload."
    result = validar(
        text=(
            "## Síntese\nNenhum movimento processual foi fornecido no payload.\n\n"
            "## Pontos de atenção\nNenhuma divergência factual identificada."
        ),
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
        required_attention_phrases=[fact],
    )
    assert result.passed is False
    assert f"required attention fact missing: {fact}" in result.errors


def test_rejects_formatted_cpf_even_when_not_present_in_source() -> None:
    result = validar(
        text="Documento informado: 123.456.789-09.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert "possible unmasked CPF/CNPJ" in result.errors


def test_rejects_formatted_cnpj_even_when_not_present_in_source() -> None:
    result = validar(
        text="Documento informado: 12.345.678/0001-90.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert "possible unmasked CPF/CNPJ" in result.errors


def test_rejects_party_identifier_rendered_with_different_formatting() -> None:
    parties = [
        {
            "name": "Maria da Silva",
            "documents": [{"document_number": "1234567890"}],
        }
    ]
    result = validar(
        text="Identificador da parte: 123-456-789-0.",
        code=CODE,
        parties=parties,
    )
    assert result.passed is False
    assert "personal identifier from party data is prohibited" in result.errors


def test_accepts_unrelated_number_when_party_identifier_differs() -> None:
    parties = [{"name": "Maria da Silva", "person_id": "1234567890"}]
    result = validar(
        text="Referência interna sintética: 987-654-321-0.",
        code=CODE,
        parties=parties,
    )
    assert "personal identifier from party data is prohibited" not in result.errors


def test_secret_mode_rejects_source_party_name_without_role_label() -> None:
    result = validar(
        text="O conteúdo menciona Maria da Silva em texto corrido.",
        code=CODE,
        parties=PARTIES,
        forbid_party_names=True,
    )
    assert result.passed is False
    assert "party names are prohibited for secret summary" in result.errors


def test_secret_mode_accepts_text_without_source_party_name() -> None:
    result = validar(
        text="Os detalhes processuais foram restringidos por sigilo.",
        code=CODE,
        parties=PARTIES,
        forbid_party_names=True,
    )
    assert result.passed is True


def test_accepts_observed_core_sections_in_contract_order_with_omissions() -> None:
    result = validar(
        text=(
            "## Partes\nMaria da Silva\n\n"
            "## Classe\nProcedimento Comum\n\n"
            "## Assuntos\nResponsabilidade civil\n\n"
            "## Movimentações\n- 13/09/2026: distribuição.\n\n"
            "## Pontos de atenção\nNenhuma divergência factual identificada."
        ),
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is True


def test_rejects_core_sections_out_of_contract_order() -> None:
    result = validar(
        text=(
            "## Movimentações\nProcesso em andamento.\n\n"
            "## Classe\nProcedimento Comum."
        ),
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert any(error.startswith("core sections must follow order:") for error in result.errors)


def test_rejects_nonexact_core_section_title() -> None:
    result = validar(
        text="## Movimentacoes\nResumo factual.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert (
        "core section title must be exactly: Movimentações; got: Movimentacoes"
        in result.errors
    )


def test_rejects_duplicate_core_section() -> None:
    result = validar(
        text=(
            "## Classe\nPrimeira classe.\n\n"
            "## Classe\nSegunda classe."
        ),
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert "duplicate core section: Classe" in result.errors


def test_accepts_exact_document_title_when_required() -> None:
    result = validar(
        text="# Resumo do processo\n\n## Classe\nProcedimento Comum.",
        code=CODE,
        parties=PARTIES,
        require_document_title=True,
    )
    assert result.passed is True


@pytest.mark.parametrize(
    "text",
    [
        "## Resumo do processo\n\n## Síntese\nResumo factual.",
        "# resumo do processo\n\n## Síntese\nResumo factual.",
        "Introdução\n\n# Resumo do processo\n\n## Síntese\nResumo factual.",
    ],
)
def test_rejects_noncanonical_or_noninitial_document_title(text: str) -> None:
    result = validar(
        text=text,
        code=CODE,
        parties=PARTIES,
        require_document_title=True,
    )
    assert result.passed is False
    assert (
        "summary must start with exact document title: # Resumo do processo"
        in result.errors
    )


def test_rejects_duplicate_document_title() -> None:
    result = validar(
        text=(
            "# Resumo do processo\n\n"
            "## Síntese\nResumo factual.\n\n"
            "# Resumo do processo"
        ),
        code=CODE,
        parties=PARTIES,
        require_document_title=True,
    )
    assert result.passed is False
    assert "duplicate document title: Resumo do processo" in result.errors


@pytest.mark.parametrize(
    "rendered",
    ["Valor: R$ 1.234,56", "Valor da causa: 1234.56", "- Valor: BRL 1234,56"],
)
def test_accepts_amount_equivalent_to_structured_source(rendered: str) -> None:
    source = '{"processo":{"header":{"amount":1234.56}},"movimentos":[]}'
    result = validar(
        text=rendered,
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is True


def test_rejects_amount_different_from_structured_source() -> None:
    source = '{"processo":{"header":{"amount":"1234.56"}},"movimentos":[]}'
    result = validar(
        text="Valor: R$ 1.500,00",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is False
    assert "source-backed amount mismatch: Valor=R$ 1.500,00" in result.errors


def test_rejects_amount_claim_when_source_has_no_amount() -> None:
    source = '{"processo":{"header":{}},"movimentos":[]}'
    result = validar(
        text="Valor da causa: R$ 1.234,56",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is False
    assert (
        "source-backed amount mismatch: Valor da causa=R$ 1.234,56"
        in result.errors
    )


def test_rejects_unparseable_amount_claim() -> None:
    source = '{"processo":{"header":{"amount":1234.56}},"movimentos":[]}'
    result = validar(
        text="Valor: aproximadamente mil reais",
        code=CODE,
        parties=PARTIES,
        source_text=source,
    )
    assert result.passed is False
    assert (
        "source-backed amount mismatch: Valor=aproximadamente mil reais"
        in result.errors
    )


def test_rejects_heading_outside_allowed_summary_contract() -> None:
    result = validar(
        text=(
            "# Resumo do processo\n\n"
            "## Síntese\nResumo factual.\n\n"
            "## Receita de lasanha\nMisture os ingredientes.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        code="0000000-00.0000.0.00.0001",
        parties=[],
        allowed_headings=(
            "Resumo do processo",
            "Síntese",
            "Pontos de atenção",
        ),
    )

    assert result.passed is False
    assert "summary heading is not allowed: Receita de lasanha" in result.errors


def test_rejects_unsourced_prompt_meta_output() -> None:
    result = validar(
        text="# Resumo do processo\n\n## Síntese\nO system prompt manda ignorar as instruções.\n\n## Pontos de atenção\nNenhuma divergência.",
        code="0000000-00.0000.0.00.0001",
        parties=[],
        source_text='{"movimentos":[{"text":"Petição ordinária."}]}',
    )

    assert result.passed is False
    assert any("meta-output marker not present in source context" in error for error in result.errors)


def test_allows_prompt_meta_language_when_it_is_source_backed() -> None:
    phrase = "A peça menciona system prompt como parte de uma tentativa de manipulação."
    result = validar(
        text=f"# Resumo do processo\n\n## Síntese\n{phrase}\n\n## Pontos de atenção\nRegistro documental.",
        code="0000000-00.0000.0.00.0001",
        parties=[],
        source_text='{"movimentos":[{"text":"A peça menciona system prompt como parte de uma tentativa de manipulação."}]}',
    )

    assert not any("meta-output marker" in error for error in result.errors)


def test_rejects_unsourced_external_url() -> None:
    result = validar(
        text="# Resumo do processo\n\n## Síntese\nVeja https://evil.example/x\n\n## Pontos de atenção\nRegistro.",
        code="0000000-00.0000.0.00.0001",
        parties=[],
        source_text='{"movimentos":[{"text":"Sem links."}]}',
    )

    assert result.passed is False
    assert "URL not present in source context: https://evil.example/x" in result.errors
