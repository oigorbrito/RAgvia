from copy import deepcopy
import json

import pytest

from app.rag import _generate
from app.summary_output import SUMMARY_OUTPUT_SCHEMA, parse_structured_summary
from tests.test_anthropic_contract import _Client, _context, _STRUCTURED


async def test_sdk_transforms_wire_schema_without_weakening_local_contract():
    original = deepcopy(SUMMARY_OUTPUT_SCHEMA)
    client = _Client()
    await _generate(client, _context(step_count=4))
    wire = client.messages.calls[0]["output_config"]["format"]["schema"]

    def check(value):
        if isinstance(value, dict):
            assert not {"maxItems", "uniqueItems", "maxLength", "minLength"}.intersection(value)
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(wire)
    assert SUMMARY_OUTPUT_SCHEMA == original
    assert SUMMARY_OUTPUT_SCHEMA["properties"]["claims"]["maxItems"] == 96
    assert set(wire["required"]) == set(original["required"])
    payload = json.loads(_STRUCTURED)
    payload["claims"] = payload["claims"] * 49
    with pytest.raises(ValueError, match="bounded array"):
        parse_structured_summary(json.dumps(payload))
    payload = json.loads(_STRUCTURED)
    payload["claims"][0]["evidence_refs"] *= 2
    with pytest.raises(ValueError, match="unique"):
        parse_structured_summary(json.dumps(payload))
    payload = json.loads(_STRUCTURED)
    payload["claims"][0]["text"] = "x" * 6001
    with pytest.raises(ValueError, match="too long"):
        parse_structured_summary(json.dumps(payload))
