"""Stable public vocabulary shared by StoryCanvas kernels, plugins, and hosts.

The existing ``storycanvas/v1`` Pydantic records remain canonical during the
compatibility migration.  New cross-plugin names live here so plugin packages
do not need to import the execution engine or a concrete host.
"""

from ..schemas import (
    ArtifactRecord,
    AttemptRecord,
    CanvasPlan,
    ExecutionPolicy,
    PlannedReference,
    ProviderReceipt,
    RunManifest,
    ShotInput,
    StoryInput,
)
from .capabilities import (
    CANVAS_RENDER,
    EVALUATION_RUN,
    FACT_SEARCH,
    IMAGE_GENERATE,
    IMAGE_PROMPT_COMPILE,
    REFERENCE_PLAN,
    STORY_PLAN,
    VIDEO_GENERATE,
    VIDEO_PROMPT_COMPILE,
    VISUAL_SEARCH,
)

PLUGIN_API_VERSION = "storycanvas/plugin/v1"
PROFILE_API_VERSION = "storycanvas/profile/v1"
PACK_API_VERSION = "storycanvas/pack/v1"

__all__ = [
    "ArtifactRecord",
    "AttemptRecord",
    "CANVAS_RENDER",
    "CanvasPlan",
    "EVALUATION_RUN",
    "ExecutionPolicy",
    "FACT_SEARCH",
    "IMAGE_GENERATE",
    "IMAGE_PROMPT_COMPILE",
    "PLUGIN_API_VERSION",
    "PROFILE_API_VERSION",
    "PACK_API_VERSION",
    "PlannedReference",
    "ProviderReceipt",
    "REFERENCE_PLAN",
    "RunManifest",
    "STORY_PLAN",
    "ShotInput",
    "StoryInput",
    "VIDEO_GENERATE",
    "VIDEO_PROMPT_COMPILE",
    "VISUAL_SEARCH",
]
