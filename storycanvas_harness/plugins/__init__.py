"""Plugin SDK, registry, profiles, and built-in compatibility adapters."""

from .api import (
    PluginContext,
    PluginManifest,
    PluginSnapshot,
    PluginStatus,
    ServicePlugin,
    StoryCanvasPlugin,
)
from .builtin import build_builtin_registry, builtin_plugin_catalog
from .discovery import (
    ENTRY_POINT_GROUP,
    installed_entry_points,
    load_requested_plugin_factories,
)
from .manifest import load_plugin_manifest
from .pack import PackManifest, load_pack
from .profile import StoryCanvasProfile, load_profile
from .registry import PluginRegistry

__all__ = [
    "PluginContext",
    "ENTRY_POINT_GROUP",
    "PluginManifest",
    "PluginRegistry",
    "PluginSnapshot",
    "PluginStatus",
    "PackManifest",
    "ServicePlugin",
    "StoryCanvasPlugin",
    "StoryCanvasProfile",
    "build_builtin_registry",
    "builtin_plugin_catalog",
    "installed_entry_points",
    "load_requested_plugin_factories",
    "load_plugin_manifest",
    "load_pack",
    "load_profile",
]
