from __future__ import annotations

from uuid import uuid4

import pytest

from app.retrieval import load_steps


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""

    async def fetch(self, query, *args):
        self.query = query
        return self.rows


@pytest.mark.asyncio
async def test_load_steps_exposes_source_step_number_from_metadata_projection() -> None:
    step_id = uuid4()
    conn = _Conn(
        [
            {
                "id": step_id,
                "step_number": 1,
                "title": "Movimento",
                "text": "Conteúdo",
                "occurred_at": None,
                "source_step_number": 17,
            }
        ]
    )

    steps = await load_steps(conn, version_id=uuid4())

    assert len(steps) == 1
    assert steps[0].id == step_id
    assert steps[0].step_number == 1
    assert steps[0].source_step_number == 17
    assert "metadata->>'source_step_number'" in conn.query


@pytest.mark.asyncio
async def test_load_steps_keeps_missing_source_step_number_as_none() -> None:
    conn = _Conn(
        [
            {
                "id": uuid4(),
                "step_number": 1,
                "title": None,
                "text": "Conteúdo",
                "occurred_at": None,
                "source_step_number": None,
            }
        ]
    )

    steps = await load_steps(conn, version_id=uuid4())

    assert steps[0].source_step_number is None
