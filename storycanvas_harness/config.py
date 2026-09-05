"""Local configuration, without provider construction or credential disclosure."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_mode: Literal["openai", "mock", "codex"] = "openai"
    runs_dir: Path = Path("runs")
    codex_enabled: bool = False
    codex_model: str = "gpt-5.6-sol"
    codex_effort: str = "medium"
    codex_bin: str = "codex"

    @classmethod
    def from_environment(cls, *, runs_dir: str | Path | None = None) -> RuntimeSettings:
        return cls.model_validate(
            {
                "provider_mode": os.getenv("STORYCANVAS_PROVIDER_MODE", "openai")
                .strip()
                .casefold(),
                "runs_dir": Path(
                    runs_dir
                    if runs_dir is not None
                    else os.environ.get("STORYCANVAS_RUNS_DIR", "runs")
                )
                .expanduser()
                .resolve(),
                "codex_enabled": os.getenv("STORYCANVAS_CODEX_ENABLED", "false"),
                "codex_model": os.getenv("STORYCANVAS_CODEX_MODEL") or "gpt-5.6-sol",
                "codex_effort": os.getenv("STORYCANVAS_CODEX_REASONING_EFFORT") or "medium",
                "codex_bin": os.getenv("STORYCANVAS_CODEX_BIN") or "codex",
            }
        )
