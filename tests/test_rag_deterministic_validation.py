from app.rag import _provider_payload, _validate_provider_summary


def _context() -> dict:
    return {
        "code": "0000000-00.2026.8.21.0114",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "subjects": [{"name": "Obrigação"}],
        "parties": [{"name": "Maria da Silva"}],
        "secrecy_level": 0,
        "header": {"distribution_date": "2026-09-10"},
        "step_count": 200,
        "_process_evidence_ref": "p-00000000000000000000000000000001",
        "_selected_sources": [],
        "_attachment_sources": [],
        "_parsed_summary": {
        "synthesis": "Síntese factual.",
        "timeline": [],
        "current_status": "Situação atual registrada.",
        "attention": ["Nenhuma divergência objetiva identificada."],
        "decisions": [],
        "deadlines": [],
        "related_processes": [],
        "attachments": [],
        "claims": [
            {
                "claim_id": "synthesis",
                "text": "Síntese factual.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
            {
                "claim_id": "current_status",
                "text": "Situação atual registrada.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
            {
                "claim_id": "attention:0",
                "text": "Nenhuma divergência objetiva identificada.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
        ],
    },
        "steps": [
            {
                "step_number": number,
                "occurred_at": "2026-09-15T12:00:00+00:00",
                "title": "Movimento",
                "text": f"Movimento recuperado {number}",
            }
            for number in range(1, 21)
        ],
    }


def test_provider_payload_exposes_total_count_not_only_retrieved_count() -> None:
    process, steps = _provider_payload(_context())
    assert process["step_count"] == 200
    assert len(steps) == 20


def test_render_only_metadata_is_not_sent_to_provider() -> None:
    context = _context()
    context["header"] = {
        "instance": 1,
        "phase": "PRIVATE_PHASE_SENTINEL",
        "status": "PRIVATE_STATUS_SENTINEL",
        "distribution_date": "2026-09-10",
        "judge": "PRIVATE_JUDGE_SENTINEL",
        "court_unit": "PRIVATE_COURT_SENTINEL",
    }
    context["representatives"] = [
        {"name": "PRIVATE_REPRESENTATIVE_SENTINEL"}
    ]

    process, _ = _provider_payload(context)

    assert "representatives" not in process  # nosec B101 - pytest assertion
    assert process["header"] == {"instance": 1}  # nosec B101 - pytest assertion
    assert not any(  # nosec B101 - pytest assertion
        sentinel in repr(process)
        for sentinel in (
            "PRIVATE_PHASE_SENTINEL",
            "PRIVATE_STATUS_SENTINEL",
            "PRIVATE_JUDGE_SENTINEL",
            "PRIVATE_COURT_SENTINEL",
            "PRIVATE_REPRESENTATIVE_SENTINEL",
        )
    )


def test_wired_validation_accepts_renderer_only_distribution_date_from_source() -> None:
    context = _context()
    provider_process, _ = _provider_payload(context)
    assert "distribution_date" not in provider_process["header"]  # nosec B101 - pytest assertion

    result = _validate_provider_summary(
        (
            "# Resumo do processo\n"
            "O processo possui 200 movimentos e foi distribuído em 10/09/2026.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        context,
    )
    assert result.passed is True


def test_wired_validation_rejects_retrieved_count_as_total() -> None:
    result = _validate_provider_summary(
        (
            "# Resumo do processo\nO processo possui 20 movimentos.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        _context(),
    )
    assert result.passed is False
    assert "movement count mismatch: stated 20, expected 200" in result.errors


def test_wired_validation_rejects_date_not_sent_to_provider() -> None:
    result = _validate_provider_summary(
        (
            "# Resumo do processo\nAudiência em 16/09/2026.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        _context(),
    )
    assert result.passed is False
    assert "date not present in source context: 2026-09-16" in result.errors


def test_wired_validation_requires_nonempty_attention_section() -> None:
    result = _validate_provider_summary("# Resumo do processo\nProcesso em andamento.", _context())
    assert result.passed is False
    assert "Pontos de atenção section is required" in result.errors
