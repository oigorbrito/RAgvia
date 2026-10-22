from __future__ import annotations

import json

import pytest


@pytest.fixture(scope="session")
def parser_rejected_json_depth(record_testsuite_property) -> int:
    """Find a native parser rejection; candidates are a fixture budget, not policy."""
    for depth in (2000, 4000, 8000, 16000):
        try:
            json.loads("[" * depth + "0" + "]" * depth)
        except RecursionError:
            record_testsuite_property("parser_rejected_json_depth", depth)
            return depth
    raise RuntimeError("Native parser accepted the bounded fixtures; revise this runtime's fixture")
