"""Built-in plugins that preserve the v0.1 provider behavior."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import PluginError
from ..protocol import (
    CANVAS_RENDER,
    FACT_SEARCH,
    IMAGE_GENERATE,
    STORY_PLAN,
    VIDEO_GENERATE,
    VISUAL_SEARCH,
)
from ..providers.director import DeterministicDirector
from ..providers.image import MockImageProvider
from ..providers.search import MockFactSearch, MockVisualSearch
from ..providers.video import MockVideoProvider
from ..schemas import CanvasPlan, CompiledWorkflow, ExecutionPolicy
from ..workflow import compile_workflow
from .api import PluginManifest, ServicePlugin, StoryCanvasPlugin
from .discovery import load_requested_plugin_factories
from .profile import StoryCanvasProfile
from .registry import PluginRegistry


class ComfyUIWorkflowRenderer:
    name = "comfyui"
    model = "storycanvas-workflow-compiler-v1"

    def compile(self, plan: CanvasPlan, policy: ExecutionPolicy) -> CompiledWorkflow:
        return compile_workflow(plan, policy)


def _plugin(
    plugin_id: str,
    capability: str,
    service: Any,
    description: str,
    *,
    permissions: list[str] | None = None,
) -> ServicePlugin:
    return ServicePlugin(
        manifest=PluginManifest(
            plugin_id=plugin_id,
            version="0.1.0",
            description=description,
            provides=[capability],
            permissions=permissions or [],
        ),
        services={capability: service},
    )


def builtin_plugin_catalog() -> dict[str, Callable[[], ServicePlugin]]:
    """Return factories so every registry receives fresh lifecycle instances."""

    return {
        "storycanvas.director.basic": lambda: _plugin(
            "storycanvas.director.basic",
            STORY_PLAN,
            DeterministicDirector(),
            "Credential-free deterministic StoryCanvas Director.",
        ),
        "storycanvas.search.fact.mock": lambda: _plugin(
            "storycanvas.search.fact.mock",
            FACT_SEARCH,
            MockFactSearch(),
            "Deterministic factual-search fixture.",
        ),
        "storycanvas.search.visual.mock": lambda: _plugin(
            "storycanvas.search.visual.mock",
            VISUAL_SEARCH,
            MockVisualSearch(),
            "Deterministic visual-search fixture.",
        ),
        "storycanvas.image.mock": lambda: _plugin(
            "storycanvas.image.mock",
            IMAGE_GENERATE,
            MockImageProvider(),
            "Deterministic local image generator for demos and tests.",
            permissions=["filesystem:run-dir"],
        ),
        "storycanvas.video.mock": lambda: _plugin(
            "storycanvas.video.mock",
            VIDEO_GENERATE,
            MockVideoProvider(),
            "Deterministic local video generator for demos and tests.",
            permissions=["filesystem:run-dir", "subprocess:ffmpeg"],
        ),
        "storycanvas.renderer.comfyui": lambda: _plugin(
            "storycanvas.renderer.comfyui",
            CANVAS_RENDER,
            ComfyUIWorkflowRenderer(),
            "Deterministic ComfyUI UI/API workflow compiler.",
        ),
    }


def build_builtin_registry(
    profile: StoryCanvasProfile,
    *,
    root: Path,
    extra_catalog: dict[str, Callable[[], StoryCanvasPlugin]] | None = None,
    discover_installed: bool = True,
) -> PluginRegistry:
    catalog: dict[str, Callable[[], StoryCanvasPlugin]] = {
        **builtin_plugin_catalog(),
        **(extra_catalog or {}),
    }
    if discover_installed:
        unresolved = [plugin_id for plugin_id in profile.plugins if plugin_id not in catalog]
        catalog.update(load_requested_plugin_factories(unresolved))
    registry = PluginRegistry(
        root=root,
        bindings=profile.bindings,
        plugin_config=profile.plugin_config,
        allowed_permissions=profile.allow_permissions,
    )
    for plugin_id in profile.plugins:
        factory = catalog.get(plugin_id)
        if factory is None:
            raise PluginError(f"Profile {profile.name!r} references unknown plugin {plugin_id!r}")
        plugin = factory()
        if plugin.manifest.plugin_id != plugin_id:
            raise PluginError(
                f"Plugin factory {plugin_id!r} returned manifest id {plugin.manifest.plugin_id!r}"
            )
        registry.register(plugin)
    registry.activate_all()
    return registry
