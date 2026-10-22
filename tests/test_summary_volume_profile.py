import json
import pytest

import app.rag as rag
from app.rag import summary_volume_instruction


@pytest.mark.parametrize(
    ("step_count", "expected"),
    [
        (0, "Processo curto"),
        (15, "Processo curto"),
        (16, "Processo de volume intermediário"),
        (60, "Processo de volume intermediário"),
        (61, "Processo longo"),
        (200, "Processo longo"),
    ],
)
def test_summary_volume_instruction_uses_documented_bands(
    step_count: int,
    expected: str,
) -> None:
    instruction = summary_volume_instruction(step_count)

    assert expected in instruction
    assert "palavras" not in instruction.lower()
    assert "tokens" not in instruction.lower()


def test_summary_volume_instruction_rejects_negative_count() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        summary_volume_instruction(-1)


class _Block:
    type = "text"
    text = json.dumps({"synthesis":"Síntese.","timeline":[],"current_status":"Situação registrada.","attention":["Nenhuma divergência objetiva identificada."],"decisions":[],"deadlines":[],"related_processes":[],"attachments":[]}, ensure_ascii=False)


class _Message:
    content = [_Block()]
    usage = {}


class _Messages:
    def __init__(self) -> None:
        self.request: dict | None = None

    async def create(self, **kwargs):
        self.request = kwargs
        return _Message()


class _Client:
    def __init__(self) -> None:
        self.messages = _Messages()


@pytest.mark.asyncio
async def test_generate_sends_volume_profile_in_real_user_prompt(monkeypatch) -> None:
    client = _Client()

    async def call_once(factory, **kwargs):
        return await factory()

    monkeypatch.setattr(rag, "call_with_retries", call_once)
    context = {
        "code": "0000000-00.2026.8.21.0001",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "subjects": [],
        "parties": [],
        "header": {},
        "secrecy_level": 0,
        "step_count": 61,
        "steps": [],
    }

    await rag._generate(client, context)

    assert client.messages.request is not None
    prompt = client.messages.request["messages"][0]["content"]
    assert "<perfil_de_extensao>" in prompt
    assert "Processo longo (mais de 60 movimentos)" in prompt
    assert "<processo_json>" in prompt
    assert "<movimentos_json>" in prompt
