from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.rag import _movement_temporal_facts, _provider_payload, _serialize_steps


def test_temporal_facts_separate_movement_date_from_scheduled_hearing() -> None:
    text = (
        "Disponibilizado em 10/02/2026. Audiência de conciliação designada "
        "para 03/03/2027 às 15:00."
    )

    facts = _movement_temporal_facts(text)

    assert facts == {  # nosec B101 - pytest assertion
        "scheduled_hearing": {"date": "03/03/2027", "time": "15:00"}
    }


def test_negated_or_cancelled_hearing_is_not_annotated_as_scheduled() -> None:
    assert _movement_temporal_facts(  # nosec B101 - pytest assertion
        "Audiência não designada para 03/03/2027 às 15:00."
    ) == {}
    assert _movement_temporal_facts(  # nosec B101 - pytest assertion
        "Audiência designada para 03/03/2027, mas posteriormente cancelada."
    ) == {}


def test_only_explicit_period_is_extracted_without_calculating_due_date() -> None:
    facts = _movement_temporal_facts("Intimação. Prazo: 15 dias úteis.")

    assert facts == {"explicit_period": {"value": 15, "unit_text": "dias úteis"}}  # nosec B101 - pytest assertion
    assert _movement_temporal_facts("Intimação em 10/02/2026. Manifestar em 15 dias.") == {}  # nosec B101 - pytest assertion


def test_temporal_facts_are_sent_with_their_source_movement() -> None:
    step = SimpleNamespace(
        id=uuid4(),
        step_number=7,
        occurred_at=datetime(2026, 2, 10, 12, tzinfo=timezone.utc),
        title="Audiência designada",
        text="Audiência de conciliação designada para 03/03/2027 às 15:00.",
    )

    serialized = _serialize_steps([SimpleNamespace(step=step)])
    _, provider_steps = _provider_payload(
        {
            "code": "0000000-00.2026.8.21.0001",
            "class_name": "Procedimento Comum",
            "court": "TJRS",
            "subjects": [],
            "parties": [],
            "secrecy_level": 0,
            "header": {},
            "steps": serialized,
        }
    )

    assert serialized[0]["occurred_at"] != "03/03/2027"  # nosec B101 - pytest assertion
    assert provider_steps[0]["source_temporal_facts"] == {  # nosec B101 - pytest assertion
        "scheduled_hearing": {"date": "03/03/2027", "time": "15:00"}
    }
