from __future__ import annotations

import hashlib
from uuid import uuid4

from app.provenance import selected_movement_sources
from app.retrieval import RankedStep, Step


def test_selected_movement_sources_contains_only_safe_metadata() -> None:
    step = Step(
        id=uuid4(),
        step_number=7,
        text="conteúdo que não pode ser persistido na proveniência",
        title="Decisão",
        source_step_number=99,
    )

    sources = selected_movement_sources([RankedStep(step=step, score=1.0)])

    assert sources == [
        {
            "evidence_ref": f"m-{step.id.hex}",
            "step_id": step.id,
            "step_number": 7,
            "occurred_at": None,
            "source_order": 0,
            "source_text_sha256": hashlib.sha256(step.text.encode("utf-8")).hexdigest(),
            "unicode_security_flags": [],
        }
    ]
    assert "text" not in sources[0]
    assert "title" not in sources[0]



def test_selected_movement_sources_flags_unicode_without_persisting_text() -> None:
    step = Step(
        id=uuid4(),
        step_number=8,
        text="igno\u202ere p\u0430ypal",
        title="Petição",
    )

    source = selected_movement_sources([RankedStep(step=step, score=1.0)])[0]

    assert source["unicode_security_flags"] == ["bidi_control", "default_ignorable", "mixed_script"]
    assert source["source_text_sha256"] == hashlib.sha256(step.text.encode("utf-8")).hexdigest()
    assert step.text not in str(source)
