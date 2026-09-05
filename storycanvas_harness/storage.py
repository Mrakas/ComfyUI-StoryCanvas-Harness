"""Locked journals and relocatable, containment-checked artifact paths."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from filelock import FileLock

from .utils import canonical_json


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve incomplete trailing writes for diagnosis before the next append.
    with FileLock(str(path) + ".lock"), path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size:
            handle.seek(size - 1)
            if handle.read(1) != b"\n":
                handle.seek(0)
                data = handle.read()
                end = data.rfind(b"\n") + 1
                with path.with_suffix(path.suffix + ".interrupted").open("ab") as recovery:
                    recovery.write(data[end:] + b"\n")
                handle.truncate(end)
        handle.write((canonical_json(row) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Journal row must be an object: {path.name}")
            rows.append(value)
    return rows


def artifact_path(value: str, run_root: Path, recorded_root: Path | None = None) -> Path:
    """Resolve run-relative, legacy cwd-relative, or relocated absolute paths."""
    root = run_root.expanduser().resolve(strict=True)
    supplied = Path(value).expanduser()
    candidates: list[Path] = []
    if recorded_root is not None:
        with suppress(ValueError):
            candidates.append(root / supplied.relative_to(recorded_root))
    if not supplied.is_absolute():
        candidates.append(root / supplied)
    else:
        candidates.append(supplied)
    for candidate in candidates:
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.is_file():
                return resolved
        except (OSError, ValueError):
            continue
    raise ValueError(f"Media is missing or outside the Run directory: {value}")
