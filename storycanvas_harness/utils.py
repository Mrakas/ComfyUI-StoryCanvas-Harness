from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True)
    if isinstance(item, Path):
        return str(item)
    if isinstance(item, Enum):
        return item.value
    if isinstance(item, datetime):
        return item.isoformat()
    raise TypeError(f"Object of type {type(item).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Stable JSON used for cache keys, receipts, and reproducibility manifests."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_json(path: str | Path, value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    atomic_write_text(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n",
    )


def slugify(value: str, *, fallback: str = "storycanvas") -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
    compact = "-".join(part for part in normalized.split("-") if part)
    return compact[:64] or fallback


def ensure_safe_id(value: str, *, label: str = "identifier") -> str:
    """Reject path separators and other unsafe characters at API/file boundaries."""
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value
