from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CONSTRAINTS = ROOT / "requirements" / "constraints.txt"
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _requirement_name(requirement: str) -> str:
    match = _NAME_RE.match(requirement)
    assert match is not None, f"invalid requirement: {requirement!r}"
    return _normalized_name(match.group(1))


def _locked_versions() -> dict[str, str]:
    locked: dict[str, str] = {}
    for raw_line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"constraint must use an exact == pin: {line}"
        name, version = line.split("==", 1)
        normalized = _normalized_name(name.strip())
        assert normalized not in locked, f"duplicate locked dependency: {normalized}"
        assert version.strip(), f"missing locked version: {line}"
        locked[normalized] = version.strip()
    return locked


def test_all_declared_python_dependencies_have_exact_lock_entries() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = list(project["project"]["dependencies"])
    for requirements in project["project"].get("optional-dependencies", {}).values():
        declared.extend(requirements)

    locked = _locked_versions()
    missing = sorted(
        name for requirement in declared if (name := _requirement_name(requirement)) not in locked
    )
    assert not missing, f"declared dependencies missing from constraints lock: {missing}"


def test_build_backend_is_exactly_pinned() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    build_requirements = project["build-system"]["requires"]
    assert build_requirements
    for requirement in build_requirements:
        assert "==" in requirement, f"build dependency must be exactly pinned: {requirement}"
