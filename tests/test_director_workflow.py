from __future__ import annotations

import json
from pathlib import Path

from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.schemas import (
    ExecutionPolicy,
    ShotInput,
    StoryInput,
    UserReference,
)
from storycanvas_harness.utils import sha256_file
from storycanvas_harness.workflow import compile_workflow

ALLOWED_CLASSES = {
    "StoryCanvasInput",
    "StoryCanvasExecutionPolicy",
    "StoryCanvasDirector",
    "StoryCanvasSharedVisualAsset",
    "StoryCanvasReferenceAsset",
    "StoryCanvasReferencePack",
    "StoryCanvasH3PromptCompiler",
    "StoryCanvasMiniMaxH3API",
    "StoryCanvasStoryAssemble",
    "StoryCanvasRunManifest",
}


def test_story_plan_has_explicit_continuity_chain() -> None:
    plan = DeterministicDirector().plan(
        StoryInput(
            title="Three beats",
            shots=[
                ShotInput(prompt="A bird wakes."),
                ShotInput(prompt="The bird flies across the room."),
                ShotInput(prompt="The bird returns to the workbench."),
            ],
        ),
        ExecutionPolicy(),
    )
    assert [shot.order for shot in plan.shots] == [1, 2, 3]
    assert plan.shots[0].previous_shot_id is None
    assert plan.shots[1].previous_shot_id == plan.shots[0].shot_id
    assert plan.shots[0].keyframe_asset_id in plan.shots[1].dependencies
    assert all(len(shot.references) <= 5 for shot in plan.shots)


def test_user_reference_order_is_preserved(reference_image: Path) -> None:
    second = reference_image.with_name("second.png")
    second.write_bytes(reference_image.read_bytes())
    shot = ShotInput(
        prompt="A fictional character opens a blue umbrella.",
        references=[
            UserReference(
                reference_id="second",
                path=str(second),
                role="umbrella",
                sha256=sha256_file(second),
            ),
            UserReference(
                reference_id="first",
                path=str(reference_image),
                role="character",
                sha256=sha256_file(reference_image),
            ),
        ],
        fixed_reference_plan=["first", "second"],
    )
    plan = DeterministicDirector().plan(shot, ExecutionPolicy())
    assert [item.reference_id for item in plan.shots[0].references[:2]] == ["first", "second"]


def test_compiler_emits_native_subgraphs_and_safe_api_graph() -> None:
    plan = DeterministicDirector().plan(
        StoryInput(free_text="First scene.\nSecond scene."), ExecutionPolicy()
    )
    compiled = compile_workflow(plan, ExecutionPolicy())
    assert len(compiled.workflow["definitions"]["subgraphs"]) == 2
    subgraphs = compiled.workflow["definitions"]["subgraphs"]
    internal_node_ids = [node["id"] for graph in subgraphs for node in graph["nodes"]]
    internal_link_ids = [link["id"] for graph in subgraphs for link in graph["links"]]
    boundary_node_ids = [
        boundary["id"]
        for graph in subgraphs
        for boundary in (graph["inputNode"], graph["outputNode"])
    ]
    assert len(internal_node_ids) == len(set(internal_node_ids))
    assert len(internal_link_ids) == len(set(internal_link_ids))
    assert len(boundary_node_ids) == len(set(boundary_node_ids))
    assert (
        len(
            [
                node
                for node in compiled.workflow["nodes"]
                if node["type"]
                in {
                    definition["id"] for definition in compiled.workflow["definitions"]["subgraphs"]
                }
            ]
        )
        == 2
    )
    assert {node["class_type"] for node in compiled.api_workflow.values()} <= ALLOWED_CLASSES
    assert all("class_type" in node and "inputs" in node for node in compiled.api_workflow.values())
    input_node = next(
        node for node in compiled.api_workflow.values() if node["class_type"] == "StoryCanvasInput"
    )
    assert json.loads(input_node["inputs"]["payload_json"]) == plan.source_input
    video_nodes = [
        node
        for node in compiled.api_workflow.values()
        if node["class_type"] == "StoryCanvasMiniMaxH3API"
    ]
    assert all("plan" in node["inputs"] for node in video_nodes)
    serialized = json.dumps(compiled.workflow)
    assert "OPENAI_API_KEY" not in serialized
    assert "MINIMAX_H3_API_KEY" not in serialized


def test_compilation_is_stable_for_same_plan() -> None:
    plan = DeterministicDirector().plan(
        ShotInput(prompt="A toy boat crosses a pond."), ExecutionPolicy()
    )
    first = compile_workflow(plan, ExecutionPolicy())
    second = compile_workflow(plan, ExecutionPolicy())
    assert first.workflow_sha256 == second.workflow_sha256
    assert first.workflow == second.workflow
