"""Declarative distribution metadata for StoryCanvas Plugin and Skill Packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli
from pydantic import Field, field_validator

from ..protocol import PACK_API_VERSION
from ..schemas import SafeId, StrictModel
from ..utils import sha256_json


class PackManifest(StrictModel):
    id: SafeId
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    api_version: str = PACK_API_VERSION
    description: str = ""
    profile: str
    plugins: list[SafeId] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str) -> str:
        if value != PACK_API_VERSION:
            raise ValueError(f"Unsupported pack API {value!r}; expected {PACK_API_VERSION!r}")
        return value

    @property
    def sha256(self) -> str:
        return sha256_json(self)


def load_pack(path: Path) -> PackManifest:
    payload: dict[str, Any] = tomli.loads(path.read_text(encoding="utf-8"))
    return PackManifest.model_validate(payload)
