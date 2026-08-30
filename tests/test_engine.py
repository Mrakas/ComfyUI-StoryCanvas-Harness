from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.errors import PolicyViolation
from storycanvas_harness.schemas import (
    ExecutionMode,
    ExecutionPolicy,
    RunStatus,
    ShotInput,
    StoryInput,
    UserReference,
)
from storycanvas_harness.utils import sha256_file


def test_plan_only_writes_workflows_without_media_calls(mock_canvas: StoryCanvas) -> None:
    record = mock_canvas.run(ShotInput(prompt="A lantern glows in a paper village."))
    assert record.manifest.status == RunStatus.COMPLETE
    assert not record.manifest.artifacts
    assert (record.root / "canvas_plan.json").is_file()
    assert (record.root / "workflow.json").is_file()
    assert (record.root / "workflow_api.json").is_file()
    assert (record.root / "audit.html").is_file()


def test_assets_mode_records_every_actual_prompt_and_reference(
    mock_canvas: StoryCanvas, reference_image: Path
) -> None:
    story = StoryInput(
        title="Mechanical bird",
        references=[
            UserReference(
                reference_id="bird-identity",
                path=str(reference_image),
                role="character_identity",
                sha256=sha256_file(reference_image),
            )
        ],
        shots=[
            ShotInput(prompt="The mechanical bird wakes."),
            ShotInput(prompt="The same bird tests its wings."),
        ],
    )
    policy = ExecutionPolicy(
        mode=ExecutionMode.ASSETS,
        max_shots=2,
        max_image_calls=3,
        max_video_calls=0,
        max_concurrency=3,
    )
    record = mock_canvas.run(story, policy)
    assert record.manifest.status == RunStatus.COMPLETE
    images = [artifact for artifact in record.manifest.artifacts if artifact.kind == "image"]
    assert len(images) == 3
    rows = [
        json.loads(line)
        for line in (record.root / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 3
    assert all(row["actual_prompt"] and row["prompt_sha256"] for row in rows)
    shot_rows = [row for row in rows if row["asset_id"].endswith("-keyframe")]
    assert len(shot_rows) == 2
    assert all(row["input_mode"] == "text+image" for row in shot_rows)
    assert all(row["ordered_references"] for row in shot_rows)
    journals = list((record.root / "receipts").glob("*.attempts.jsonl"))
    assert len(journals) == 3
    assert all(
        json.loads(path.read_text().splitlines()[-1])["attempt"]["status"] == "success"
        for path in journals
    )


def test_valid_asset_receipts_are_reused(mock_canvas: StoryCanvas) -> None:
    value = ShotInput(prompt="A paper kite rises above a fictional harbor.")
    policy = ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=2)
    first = mock_canvas.run(value, policy)
    second = mock_canvas.run(value, policy)
    assert first.run_id == second.run_id
    assert second.manifest.call_counts["image_cache_hits"] == 2
    assert second.manifest.call_counts["image_generation"] == 0
    assert [item.sha256 for item in first.manifest.artifacts] == [
        item.sha256 for item in second.manifest.artifacts
    ]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_assets_run_can_complete_videos_without_regenerating_images(
    mock_canvas: StoryCanvas,
) -> None:
    story = StoryInput(
        title="Moon garden",
        shots=[ShotInput(prompt="Plant a seed."), ShotInput(prompt="The vine grows.")],
    )
    assets_policy = ExecutionPolicy(
        mode=ExecutionMode.ASSETS,
        max_shots=2,
        max_image_calls=3,
        max_video_calls=0,
        max_concurrency=2,
    )
    assets = mock_canvas.run(story, assets_policy)
    image_shas = {
        artifact.artifact_id: artifact.sha256
        for artifact in assets.manifest.artifacts
        if artifact.kind == "image"
    }

    completed = mock_canvas.complete_videos(
        assets.root,
        ExecutionPolicy(
            mode=ExecutionMode.FULL,
            allow_paid_video=True,
            max_shots=2,
            max_image_calls=3,
            max_video_calls=2,
            max_concurrency=1,
        ),
    )

    assert completed.root == assets.root
    assert completed.run_id == assets.run_id
    assert completed.manifest.status == RunStatus.COMPLETE
    assert completed.manifest.call_counts["image_generation"] == 3
    assert completed.manifest.call_counts["video_generation"] == 2
    assert image_shas == {
        artifact.artifact_id: artifact.sha256
        for artifact in completed.manifest.artifacts
        if artifact.kind == "image"
    }
    assert len(list((assets.root / "videos" / "shots").glob("*.mp4"))) == 2


def test_policy_violation_happens_before_any_image_call(mock_canvas: StoryCanvas) -> None:
    value = ShotInput(prompt="A compass points toward a glowing doorway.")
    policy = ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=1)
    with pytest.raises(PolicyViolation):
        mock_canvas.run(value, policy)
    assert not list(mock_canvas.runs_dir.glob("run-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_full_mock_story_generates_shots_and_assembled_video(mock_canvas: StoryCanvas) -> None:
    story = StoryInput(
        shots=[ShotInput(prompt="A door opens."), ShotInput(prompt="A traveler enters.")]
    )
    policy = ExecutionPolicy(
        mode=ExecutionMode.FULL,
        allow_paid_video=True,
        max_shots=2,
        max_image_calls=3,
        max_video_calls=2,
        max_concurrency=2,
    )
    record = mock_canvas.run(story, policy)
    assert record.manifest.status == RunStatus.COMPLETE
    assert (record.root / "videos" / "story.mp4").is_file()
    shot_videos = list((record.root / "videos" / "shots").glob("*.mp4"))
    assert len(shot_videos) == 2
    assert all(path.stat().st_size > 0 for path in shot_videos)
    audit = (record.root / "audit.html").read_text(encoding="utf-8")
    assert audit.count("<img") == 3
    assert audit.count("<video") == 2
    assert str(record.root) not in audit
