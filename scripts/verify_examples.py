"""Verify checked-in example files against their SHA-256 manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(example: Path) -> None:
    manifest = example / "MANIFEST.sha256"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = example / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path} ({actual} != {expected})")
    print(f"verified {example.relative_to(ROOT)}")


def main() -> None:
    for example in sorted((ROOT / "examples").iterdir()):
        if example.is_dir() and (example / "MANIFEST.sha256").is_file():
            verify(example)


if __name__ == "__main__":
    main()
