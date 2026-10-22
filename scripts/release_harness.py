from __future__ import annotations

import os
import stat
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _validate_release_state(
    *,
    version: str,
    tag: str,
    versioning_text: str,
    next_release_text: str,
) -> list[str]:
    errors: list[str] = []

    for required in ("Release tags are immutable", "`v0.1.0`", f"`{tag}`"):
        if required not in versioning_text:
            errors.append(
                f"release versioning policy missing required marker: {required}"
            )

    unselected = "**Release version: not yet selected**" in next_release_text
    selected = f"**Release version: {version}**" in next_release_text
    if unselected == selected:
        errors.append(
            "next release readiness must declare exactly one release state: "
            "not yet selected or the current package version"
        )
    elif unselected:
        marker = "The next release identifier has **not** been selected."
        if marker not in versioning_text:
            errors.append(
                f"release versioning policy missing required marker: {marker}"
            )
    else:
        marker = f"selected `{version}`"
        if marker not in versioning_text:
            errors.append(
                f"release versioning policy missing selected-version marker: {marker}"
            )

    for required in (
        "## Next artifact gate",
        "## Release vs activation",
        "create the immutable tag only after",
    ):
        if required not in next_release_text:
            errors.append(
                f"next release readiness missing required marker: {required}"
            )

    return errors


def main() -> int:
    errors: list[str] = []

    pyproject = ROOT / "pyproject.toml"
    metadata = tomllib.loads(read(pyproject))
    project = metadata["project"]
    version = project["version"]
    tag = f"v{version}"

    release_notes = ROOT / "docs" / "release" / f"{tag}.md"
    candidate = ROOT / "docs" / "release" / "offline-release-candidate.md"
    provider_acceptance = ROOT / "docs" / "release" / "provider-acceptance.md"
    versioning = ROOT / "docs" / "release" / "versioning.md"
    next_release = ROOT / "docs" / "release" / "next-release.md"
    readme = ROOT / "README.md"
    license_file = ROOT / "LICENSE"
    notice_file = ROOT / "NOTICE"
    smoke_sh = ROOT / "scripts" / "smoke_offline.sh"
    smoke_ps1 = ROOT / "scripts" / "smoke_offline.ps1"

    for path in (
        release_notes,
        candidate,
        provider_acceptance,
        versioning,
        next_release,
        readme,
        license_file,
        notice_file,
        smoke_sh,
        smoke_ps1,
    ):
        if not path.exists():
            errors.append(f"missing required release artifact: {path.relative_to(ROOT)}")

    if project.get("license") != "MIT":
        errors.append("pyproject project.license must be MIT for the v0 release")

    if not errors:
        release_text = read(release_notes)
        candidate_text = read(candidate)
        provider_acceptance_text = read(provider_acceptance)
        versioning_text = read(versioning)
        next_release_text = read(next_release)
        readme_text = read(readme)
        license_text = read(license_file)
        notice_text = read(notice_file)
        sh_text = read(smoke_sh)
        ps1_text = read(smoke_ps1)

        if tag not in release_text:
            errors.append(f"release notes must identify tag {tag}")
        if "`0.1.0`" not in candidate_text or "`v0.1.0`" not in candidate_text:
            errors.append("offline release candidate must retain the historical v0.1.0 evidence")
        if "- [ ]" in candidate_text:
            errors.append("offline release Definition of Done still contains unchecked items")
        for marker in (
            "## Preconditions",
            "## 1. Judit acquisition",
            "## 2. Anthropic generation",
            "## 3. Embeddings",
            "## 4. Reranking",
            "## Stop conditions",
        ):
            if marker not in provider_acceptance_text:
                errors.append(f"provider acceptance runbook missing required section: {marker}")

        errors.extend(
            _validate_release_state(
                version=version,
                tag=tag,
                versioning_text=versioning_text,
                next_release_text=next_release_text,
            )
        )

        for command in ("./scripts/smoke_offline.sh", ".\\scripts\\smoke_offline.ps1"):
            if command not in readme_text:
                errors.append(f"README missing canonical smoke command: {command}")

        if "licença MIT" not in readme_text:
            errors.append("README must declare the MIT project license")
        if not license_text.startswith("MIT License"):
            errors.append("LICENSE must contain the MIT license text")
        for marker in (
            "Projects with published permissive licenses:",
            "Projects used with direct author permission:",
            "Rpy is licensed under the MIT License.",
        ):
            if marker not in notice_text:
                errors.append(f"NOTICE missing required provenance marker: {marker}")

        mode = smoke_sh.stat().st_mode
        if sys.platform != "win32" and not mode & stat.S_IXUSR:
            errors.append("scripts/smoke_offline.sh must remain executable")
        if "docker info" not in sh_text or "trap cleanup EXIT" not in sh_text:
            errors.append("Unix smoke must fail early on Docker daemon errors and keep cleanup trap")
        if "docker info" not in ps1_text or "down -v --remove-orphans" not in ps1_text:
            errors.append("PowerShell smoke must check Docker daemon and retain best-effort cleanup")

    if errors:
        print("Release harness: FAILED")
        for error in errors:
            print(f" - {error}")
        return 1

    print(
        f"Release harness: OK (release_version={version}, release_tag={tag}, historical_tag=v0.1.0, license=MIT, canonical smoke entrypoints verified)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
