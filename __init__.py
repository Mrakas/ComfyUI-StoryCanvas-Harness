"""ComfyUI custom-node entry point for StoryCanvas Harness."""

if __package__:
    # ComfyUI loads a custom-node directory as an isolated package.
    from .storycanvas_harness.comfy_nodes import (
        NODE_CLASS_MAPPINGS,
        NODE_DISPLAY_NAME_MAPPINGS,
    )
else:
    # Test runners can collect this root entry point as a top-level module.
    from storycanvas_harness.comfy_nodes import (
        NODE_CLASS_MAPPINGS,
        NODE_DISPLAY_NAME_MAPPINGS,
    )

WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
