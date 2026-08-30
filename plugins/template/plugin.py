"""Minimal image-Prompt processor implemented against the public Plugin API."""

from __future__ import annotations

from storycanvas_harness.protocol import (
    IMAGE_PROMPT_COMPILE,
    STORY_PLAN,
    CanvasPlan,
    ExecutionPolicy,
    ShotInput,
    StoryInput,
)
from storycanvas_harness.sdk import PluginManifest, ServicePlugin


class ExamplePromptProcessor:
    name = "community-example"
    model = "rule-v1"

    def transform(
        self,
        plan: CanvasPlan,
        source: ShotInput | StoryInput,
        policy: ExecutionPolicy,
    ) -> CanvasPlan:
        del source, policy
        for shot in plan.shots:
            shot.image_prompt += " Preserve a restrained cyan-and-amber palette."
        return plan


def create_plugin() -> ServicePlugin:
    return ServicePlugin(
        manifest=PluginManifest(
            plugin_id="community.example-plugin",
            version="0.1.0",
            description="Minimal third-party image-Prompt processor template.",
            provides=[IMAGE_PROMPT_COMPILE],
            requires=[STORY_PLAN],
        ),
        services={IMAGE_PROMPT_COMPILE: ExamplePromptProcessor()},
    )
