"""Load the human-readable TOML form of a Plugin manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli

from .api import PluginManifest


def load_plugin_manifest(path: Path) -> PluginManifest:
    payload: dict[str, Any] = tomli.loads(path.read_text(encoding="utf-8"))
    if "id" in payload and "plugin_id" not in payload:
        payload["plugin_id"] = payload.pop("id")
    return PluginManifest.model_validate(payload)
