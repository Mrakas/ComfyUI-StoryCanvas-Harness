"""Small, host-neutral Plugin API for StoryCanvas.

Plugins expose named services.  The kernel owns lifecycle, dependency
resolution, policy, artifacts, and event persistence; plugins own one bounded
capability implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, field_validator

from ..protocol import PLUGIN_API_VERSION
from ..schemas import SafeId, StrictModel


class PluginStatus(str, Enum):
    DISCOVERED = "discovered"
    WAITING = "waiting"
    LOADING = "loading"
    ACTIVE = "active"
    FAILED = "failed"
    UNLOADING = "unloading"
    DISPOSED = "disposed"


class PluginManifest(StrictModel):
    plugin_id: SafeId
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    api_version: str = PLUGIN_API_VERSION
    description: str = ""
    provides: list[str] = Field(min_length=1)
    requires: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str) -> str:
        if value != PLUGIN_API_VERSION:
            raise ValueError(f"Unsupported plugin API {value!r}; expected {PLUGIN_API_VERSION!r}")
        return value

    @field_validator("provides", "requires")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Capability lists cannot contain duplicates")
        for value in values:
            if "." not in value or value.startswith(".") or value.endswith("."):
                raise ValueError(f"Invalid capability name: {value!r}")
        return values


@dataclass(slots=True)
class PluginContext:
    root: Path
    config: Mapping[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class StoryCanvasPlugin(Protocol):
    manifest: PluginManifest
    services: Mapping[str, Any]

    def start(self, context: PluginContext) -> None: ...

    def stop(self) -> None: ...


@dataclass(slots=True)
class ServicePlugin:
    """Minimal plugin implementation for wrapping an existing provider/service."""

    manifest: PluginManifest
    services: Mapping[str, Any]
    started: bool = False

    def start(self, context: PluginContext) -> None:
        del context
        self.started = True

    def stop(self) -> None:
        self.started = False


class PluginSnapshot(StrictModel):
    plugin_id: str
    version: str
    status: PluginStatus
    provides: list[str]
    requires: list[str]
    permissions: list[str]
    error: str | None = None
