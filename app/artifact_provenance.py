from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_manifest(root: Path, *, label: str = "artifact") -> dict[str, Any]:
    if not root.is_dir():
        raise RuntimeError(f"{label} directory does not exist: {root}")

    entries = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    directory_symlinks = [
        path for path in entries if path.is_symlink() and path.is_dir()
    ]
    if directory_symlinks:
        relative = directory_symlinks[0].relative_to(root).as_posix()
        raise RuntimeError(
            f"{label} contains an unsupported directory symlink: {relative}"
        )

    files = [path for path in entries if path.is_file()]
    if not files:
        raise RuntimeError(f"{label} contains no files: {root}")

    manifest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_sha256 = sha256_file(path)
        total_bytes += size
        record = json.dumps(
            [relative, size, file_sha256],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        manifest.update(record)
        manifest.update(b"\n")

    return {
        "sha256": manifest.hexdigest(),
        "file_count": len(files),
        "total_bytes": total_bytes,
    }
