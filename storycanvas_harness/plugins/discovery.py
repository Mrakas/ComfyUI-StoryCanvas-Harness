"""Opt-in discovery of installed StoryCanvas plugin factories.

Only entry points explicitly named by a loaded Profile are imported. Merely
enumerating a Profile or importing StoryCanvas does not execute third-party
plugin code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points

from ..errors import PluginError
from .api import StoryCanvasPlugin

ENTRY_POINT_GROUP = "storycanvas.plugins"
PluginFactory = Callable[[], StoryCanvasPlugin]


def installed_entry_points() -> dict[str, EntryPoint]:
    """Return installed plugin entry points without importing their packages."""

    discovered: dict[str, EntryPoint] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        if entry_point.name in discovered:
            raise PluginError(
                f"Duplicate installed StoryCanvas plugin entry point: {entry_point.name!r}"
            )
        discovered[entry_point.name] = entry_point
    return discovered


def load_requested_plugin_factories(plugin_ids: Iterable[str]) -> dict[str, PluginFactory]:
    """Load factories for requested installed plugins and no others."""

    available = installed_entry_points()
    factories: dict[str, PluginFactory] = {}
    for plugin_id in plugin_ids:
        entry_point = available.get(plugin_id)
        if entry_point is None:
            continue
        try:
            loaded = entry_point.load()
        except Exception as error:
            raise PluginError(
                f"Installed plugin {plugin_id!r} could not be imported: "
                f"{type(error).__name__}: {error}"
            ) from error
        if not callable(loaded):
            raise PluginError(
                f"Installed plugin {plugin_id!r} entry point must resolve to a factory"
            )
        factories[plugin_id] = loaded
    return factories
