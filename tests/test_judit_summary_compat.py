from datetime import datetime, timezone

import pytest

from app.judit_summary_compat import (
    judit_summary_item,
    render_judit_compatible_summary,
)
from app.summary_output import structured_summary_document


def _context():
    return {
        "code": "0000000-00.2026.8.21.0001",
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "header": {"instance": 1},
        "parties": [
            {"name": "Parte <Alfa>", "side": "active", "masked_person_id": "***.***.***-01"}
        ],
        "representatives": [
            {"name": "Representante & Beta", "represents": "Parte <Alfa>", "document": "OAB/RS 123"}
        ],
        "subjects": [{"name": "Responsabilidade civil"}],
    }


def _document():
    payload = {
        "synthesis": "Contexto do processo: cadastro processual. Andamento processual: movimento recente registrado.",
        "timeline": ["Movimento <script>alert(1)</script> registrado."],
        "current_status": "Movimento recente registrado.",
        "attention": ["O recorte não registra sentença."],
        "decisions": [],
        "deadlines": [],
        "related_processes": [],
        "attachments": [],
        "claims": [],
    }
    return structured_summary_document(payload, _context())


def test_compatibility_renderer_uses_canonical_document_and_keeps_people_separate():
    rendered = render_judit_compatible_summary(_document(), _context())

    assert rendered.startswith("# Resumo do processo")
    assert 'className="summary-process-header"' in rendered
    assert "<strong>Processo:</strong> 0000000-00.2026.8.21.0001" in rendered
    assert "<strong>Tribunal:</strong> TJRS" in rendered
    assert "## Partes" in rendered
    assert "## Representantes" in rendered
    assert "## Movimentações" in rendered
    assert "## Situação atual" not in rendered
    assert "<strong>Estado atual:</strong>" in rendered
    assert "<p><strong>Pontos de atenção</strong></p>" in rendered
    assert "Parte &lt;Alfa&gt;" in rendered
    assert "Representante &amp; Beta" in rendered
    assert "OAB/RS 123" not in rendered
    assert "***.***.***-01" not in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_compatibility_renderer_rejects_wrong_process_identity():
    with pytest.raises(ValueError, match="does not match"):
        render_judit_compatible_summary(_document(), _context() | {"code": "0000000-00.2026.8.21.0002"})


def test_judit_response_envelope_is_stable_and_preserves_summary_item_contract():
    item = judit_summary_item(
        "# Resumo do processo",
        created_at=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc),
        generator="claude-sonnet-5",
        source_refs=["step-b", "step-a", "step-b"],
    )

    assert item == {
        "response_type": "summary",
        "response_data": {
            "data": ["# Resumo do processo"],
            "origin_id": None,
            "origin": "response",
        },
        "tags": {
            "gerador": "claude-sonnet-5",
            "alertas": [],
            "fontes": ["step-a", "step-b"],
        },
        "created_at": "2026-10-26T12:00:00.000Z",
    }
