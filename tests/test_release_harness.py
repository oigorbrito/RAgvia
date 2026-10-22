from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_harness.py"
SPEC = importlib.util.spec_from_file_location("release_harness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release_harness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_harness
SPEC.loader.exec_module(release_harness)


def _base_versioning() -> str:
    return """
Release tags are immutable.
Published baselines include `v0.1.0` and `v0.1.1`.
"""


def _base_next_release(state: str) -> str:
    return f"""
{state}
## Next artifact gate
create the immutable tag only after all required gates pass.
## Release vs activation
"""


def test_release_state_accepts_unselected_next_version() -> None:
    errors = release_harness._validate_release_state(
        version="0.1.1",
        tag="v0.1.1",
        versioning_text=(
            _base_versioning()
            + "\nThe next release identifier has **not** been selected.\n"
        ),
        next_release_text=_base_next_release(
            "**Release version: not yet selected**"
        ),
    )

    assert errors == []


def test_release_state_accepts_explicit_selected_version() -> None:
    errors = release_harness._validate_release_state(
        version="0.1.2",
        tag="v0.1.2",
        versioning_text=(
            "Release tags are immutable. Published: `v0.1.0`. "
            "The release owner selected `0.1.2` for the next release. "
            "Candidate tag: `v0.1.2`."
        ),
        next_release_text=_base_next_release("**Release version: 0.1.2**"),
    )

    assert errors == []


def test_release_state_rejects_conflicting_selected_and_unselected_markers() -> None:
    errors = release_harness._validate_release_state(
        version="0.1.1",
        tag="v0.1.1",
        versioning_text=(
            _base_versioning()
            + "\nThe next release identifier has **not** been selected.\n"
        ),
        next_release_text=_base_next_release(
            "**Release version: not yet selected**\n**Release version: 0.1.1**"
        ),
    )

    assert any("exactly one release state" in error for error in errors)
