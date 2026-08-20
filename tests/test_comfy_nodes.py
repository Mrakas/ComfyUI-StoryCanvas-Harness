from __future__ import annotations

from storycanvas_harness.comfy_nodes import (
    NODE_CLASS_MAPPINGS,
    StoryCanvasDirectorNode,
    StoryCanvasExecutionPolicyNode,
    StoryCanvasH3PromptCompilerNode,
    StoryCanvasInputNode,
    StoryCanvasReferencePackNode,
)
from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.schemas import ExecutionPolicy, ShotInput


def test_all_public_node_types_are_registered() -> None:
    assert len(NODE_CLASS_MAPPINGS) == 10
    for node in NODE_CLASS_MAPPINGS.values():
        assert node.CATEGORY.startswith("StoryCanvas")
        assert node.FUNCTION


def test_precomputed_plan_round_trip_and_prompt_binding() -> None:
    policy = ExecutionPolicy()
    plan = DeterministicDirector().plan(
        ShotInput(prompt="A tiny robot waters a bonsai tree."), policy
    )
    story_input = StoryCanvasInputNode().build("shot", '{"prompt":"ignored"}')[0]
    policy_value = StoryCanvasExecutionPolicyNode().validate(policy.model_dump_json())[0]
    parsed = StoryCanvasDirectorNode().direct(
        story_input, policy_value, "precomputed", plan.model_dump_json()
    )[0]
    packed = StoryCanvasReferencePackNode().pack(
        1,
        reference_1={
            "asset_id": "canvas",
            "role": "final_canvas",
            "provenance": "generated_canvas",
            "status": "planned",
        },
    )[0]
    prompt = StoryCanvasH3PromptCompilerNode().compile(parsed, packed, plan.shots[0].shot_id)[0]
    assert "Image 1: final_canvas" in prompt["prompt"]
    assert plan.shots[0].h3_prompt in prompt["prompt"]
