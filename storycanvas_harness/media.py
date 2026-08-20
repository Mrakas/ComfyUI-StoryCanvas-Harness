from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any

from .errors import ProviderError

# subprocess is required for fixed ffmpeg argv; shell execution is never used.


def probe_media(path: str | Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ProviderError("ffprobe is required for media validation")
    # The executable is resolved locally and argv is not a shell string.
    result = subprocess.run(  # nosec B603
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ProviderError(f"ffprobe failed for {path}: {result.stderr[-1000:]}")
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def full_decode(path: str | Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProviderError("ffmpeg is required for media validation")
    # The executable is resolved locally and argv is not a shell string.
    result = subprocess.run(  # nosec B603
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=1200,
        check=False,
    )
    if result.returncode:
        raise ProviderError(f"Full decode failed for {path}: {result.stderr[-1000:]}")


def concat_videos(paths: list[Path], destination: Path) -> None:
    if not paths:
        raise ProviderError("Cannot assemble a story without shot videos")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProviderError("ffmpeg is required for story assembly")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="storycanvas-concat-") as temp_dir:
        concat_file = Path(temp_dir) / "inputs.txt"
        concat_file.write_text(
            "".join(
                f"file '{str(path.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                for path in paths
            ),
            encoding="utf-8",
        )
        # The executable is resolved locally and no shell is involved.
        copy_result = subprocess.run(  # nosec B603
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if copy_result.returncode == 0:
            return
        # The executable is resolved locally and no shell is involved.
        encode_result = subprocess.run(  # nosec B603
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if encode_result.returncode:
            raise ProviderError(f"Story assembly failed: {encode_result.stderr[-1500:]}")
