"""Declarative plugin composition profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..protocol import PROFILE_API_VERSION
from ..schemas import SafeId, StrictModel
from ..utils import sha256_json


class StoryCanvasProfile(StrictModel):
    schema_version: str = PROFILE_API_VERSION
    name: SafeId
    description: str = ""
    plugins: list[SafeId] = Field(min_length=1)
    bindings: dict[str, SafeId] = Field(default_factory=dict)
    plugin_config: dict[SafeId, dict[str, Any]] = Field(default_factory=dict)
    allow_permissions: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != PROFILE_API_VERSION:
            raise ValueError(f"Unsupported profile API {value!r}; expected {PROFILE_API_VERSION!r}")
        return value

    @model_validator(mode="after")
    def validate_composition_references(self) -> StoryCanvasProfile:
        selected = set(self.plugins)
        if len(selected) != len(self.plugins):
            raise ValueError("Profile Plugins cannot contain duplicates")
        unknown_bindings = sorted(set(self.bindings.values()) - selected)
        if unknown_bindings:
            raise ValueError(f"Profile bindings reference unselected Plugins: {unknown_bindings}")
        unknown_config = sorted(set(self.plugin_config) - selected)
        if unknown_config:
            raise ValueError(f"Profile config references unselected Plugins: {unknown_config}")
        return self

    @property
    def sha256(self) -> str:
        return sha256_json(self)


def load_profile(path: Path) -> StoryCanvasProfile:
    return StoryCanvasProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
