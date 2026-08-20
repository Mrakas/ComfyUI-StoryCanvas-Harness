from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import storycanvas_harness.comfy_nodes as comfy_nodes
from storycanvas_harness.comfy_nodes import (
    StoryCanvasH3PromptCompilerNode,
    StoryCanvasMiniMaxH3APINode,
    StoryCanvasReferenceAssetNode,
    StoryCanvasReferencePackNode,
    StoryCanvasRunManifestNode,
    StoryCanvasSharedVisualAssetNode,
    StoryCanvasStoryAssembleNode,
)
from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.providers.image import MockImageProvider
from storycanvas_harness.providers.video import MockVideoProvider
from storycanvas_harness.schemas import ExecutionMode, ExecutionPolicy, ShotInput, StoryInput


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_comfy_nodes_share_one_run_root_and_emit_complete_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = StoryCanvas(
        runs_dir=tmp_path / "runs",
        director=DeterministicDirector(),
        image_provider=MockImageProvider(),
        video_provider=MockVideoProvider(),
    )
    monkeypatch.setattr(comfy_nodes, "_ENGINE", runtime)
    policy = ExecutionPolicy(
        mode=ExecutionMode.FULL,
        allow_paid_video=True,
        max_shots=2,
        max_image_calls=3,
        max_video_calls=2,
    )
    plan = runtime.plan(
        StoryInput(
            story_id="comfy-smoke",
            shots=[
                ShotInput(shot_id="smoke-01", prompt="A paper door opens."),
                ShotInput(shot_id="smoke-02", prompt="The same paper traveler enters."),
            ],
        ),
        policy,
    )
    plan_data = plan.model_dump(mode="json", exclude_none=True)
    policy_data = policy.model_dump(mode="json", exclude_none=True)
    style = StoryCanvasSharedVisualAssetNode().resolve(plan_data, policy_data, "style-bible")[0]

    keyframes: dict[str, dict[str, object]] = {}
    videos: list[dict[str, object]] = []
    for shot in plan.shots:
        canvas_references = []
        for reference in shot.references:
            if reference.source_asset_id == "style-bible":
                canvas_references.append(style)
            elif reference.source_asset_id:
                previous_id = reference.source_asset_id.removesuffix("-keyframe")
                canvas_references.append(keyframes[previous_id])
        canvas_pack = StoryCanvasReferencePackNode().pack(
            len(canvas_references),
            **{f"reference_{index}": item for index, item in enumerate(canvas_references, start=1)},
        )[0]
        keyframe = StoryCanvasReferenceAssetNode().resolve(
            plan_data,
            policy_data,
            shot.keyframe_asset_id,
            shot.shot_id,
            "",
            canvas_pack,
        )[0]
        keyframes[shot.shot_id] = keyframe
        h3_references = [keyframe, *canvas_references]
        h3_pack = StoryCanvasReferencePackNode().pack(
            len(h3_references),
            **{f"reference_{index}": item for index, item in enumerate(h3_references, start=1)},
        )[0]
        prompt = StoryCanvasH3PromptCompilerNode().compile(plan_data, h3_pack, shot.shot_id)[0]
        video = StoryCanvasMiniMaxH3APINode().generate(
            plan_data, prompt, h3_pack, policy_data, shot.shot_id
        )[0]
        videos.append(video)

    story = StoryCanvasStoryAssembleNode().assemble(
        plan_data,
        policy_data,
        len(videos),
        **{f"video_{index}": item for index, item in enumerate(videos, start=1)},
    )[0]
    manifest_result = StoryCanvasRunManifestNode().write(plan_data, policy_data, story)[0]
    manifest = json.loads(Path(manifest_result["path"]).read_text(encoding="utf-8"))

    assert manifest_result["status"] == "complete"
    assert manifest["call_counts"] == {
        "image_generation": 3,
        "video_generation": 2,
        "visual_search_download": 0,
    }
    assert len(manifest["artifacts"]) == 6
    assert (
        len(
            (Path(manifest_result["path"]).parent / "prompts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        == 5
    )
    assert len({Path(str(item["path"])).parents[2] for item in videos}) == 1
