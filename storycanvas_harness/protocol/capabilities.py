"""Namespaced capability identifiers used by StoryCanvas Plugin API v1."""

STORY_PLAN = "story.plan"
REFERENCE_PLAN = "reference.plan"
IMAGE_PROMPT_COMPILE = "prompt.compile.image"
VIDEO_PROMPT_COMPILE = "prompt.compile.video"
FACT_SEARCH = "search.fact"
VISUAL_SEARCH = "search.visual"
IMAGE_GENERATE = "media.image.generate"
VIDEO_GENERATE = "media.video.generate"
EVALUATION_RUN = "evaluation.run"
CANVAS_RENDER = "canvas.render"

CORE_CAPABILITIES = frozenset(
    {
        STORY_PLAN,
        REFERENCE_PLAN,
        IMAGE_PROMPT_COMPILE,
        VIDEO_PROMPT_COMPILE,
        FACT_SEARCH,
        VISUAL_SEARCH,
        IMAGE_GENERATE,
        VIDEO_GENERATE,
        EVALUATION_RUN,
        CANVAS_RENDER,
    }
)
