from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest
from PIL import Image

from storycanvas_harness import review
from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.review import import_comfy_review
from storycanvas_harness.schemas import (
    ArtifactRecord,
    ExecutionMode,
    ExecutionPolicy,
    InputMode,
    PlannedReference,
    Provenance,
    RunManifest,
    RunStatus,
    ShotInput,
)
from storycanvas_harness.utils import atomic_write_json, atomic_write_text, sha256_file


def _image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), color).save(path)


def _fixture_run(tmp_path: Path) -> tuple[Path, Path]:
    run_root = tmp_path / "run-review-test"
    comfy_root = tmp_path / "ComfyUI"
    (comfy_root / "input").mkdir(parents=True)
    (comfy_root / "user").mkdir()
    plan = DeterministicDirector().plan(
        ShotInput(prompt="A gardener plants one moonlit seed.", shot_id="shot-001"),
        ExecutionPolicy(),
    )
    shot = plan.shots[0]
    style = run_root / "assets" / "style-bible.png"
    keyframe = run_root / "assets" / f"{shot.keyframe_asset_id}.png"
    midframe = run_root / "midframes" / f"{shot.shot_id}.png"
    shot_video = run_root / "videos" / "shots" / f"{shot.shot_id}.mp4"
    story_video = run_root / "videos" / "story.mp4"
    _image(style, (12, 34, 56))
    _image(keyframe, (34, 56, 78))
    _image(midframe, (56, 78, 90))
    shot_video.parent.mkdir(parents=True)
    shot_video.write_bytes(b"test-video")
    story_video.write_bytes(b"test-story-video")
    style_reference = PlannedReference(
        order=1,
        reference_id="style-reference",
        role="style",
        provenance=Provenance.IMAGE_GENERATION,
        source_asset_id="style-bible",
        path=str(style),
        sha256=sha256_file(style),
    )
    artifacts = [
        ArtifactRecord(
            artifact_id="style-bible",
            kind="image",
            path=str(style),
            sha256=sha256_file(style),
            provenance=Provenance.IMAGE_GENERATION,
            prompt="Actual style prompt",
            input_mode=InputMode.TEXT,
        ),
        ArtifactRecord(
            artifact_id=shot.keyframe_asset_id,
            kind="image",
            path=str(keyframe),
            sha256=sha256_file(keyframe),
            provenance=Provenance.IMAGE_GENERATION,
            prompt="Actual shot prompt",
            input_mode=InputMode.TEXT_IMAGE,
            ordered_references=[style_reference],
            metadata={"shot_id": shot.shot_id},
        ),
        ArtifactRecord(
            artifact_id=f"{shot.shot_id}-video",
            kind="video",
            path=str(shot_video),
            sha256=sha256_file(shot_video),
            prompt=shot.h3_prompt,
            input_mode=InputMode.TEXT_IMAGE,
            ordered_references=[style_reference],
            metadata={"shot_id": shot.shot_id},
        ),
        ArtifactRecord(
            artifact_id="story-video",
            kind="video",
            path=str(story_video),
            sha256=sha256_file(story_video),
        ),
    ]
    manifest = RunManifest(
        run_id=run_root.name,
        plan_id=plan.plan_id,
        status=RunStatus.COMPLETE,
        input_sha256=plan.input_sha256,
        policy=ExecutionPolicy(
            mode=ExecutionMode.FULL,
            allow_paid_video=True,
            max_video_calls=1,
        ),
        run_root=str(run_root),
        artifacts=artifacts,
    )
    atomic_write_json(run_root / "canvas_plan.json", plan)
    atomic_write_json(run_root / "run_manifest.json", manifest)
    rows = [
        {
            "asset_id": "style-bible",
            "planned_prompt": "Planned style prompt",
            "actual_prompt": "Actual style prompt",
        },
        {
            "asset_id": shot.keyframe_asset_id,
            "planned_prompt": shot.image_prompt,
            "actual_prompt": "Actual shot prompt",
        },
    ]
    atomic_write_text(
        run_root / "prompts.jsonl",
        "".join(json.dumps(row) + "\n" for row in rows),
    )
    (run_root / "receipts").mkdir()
    atomic_write_json(run_root / "receipts" / "style-bible.json", {"ok": True})
    return run_root, comfy_root


@pytest.fixture(autouse=True)
def stub_video_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        review, "_validate_video", lambda path: {"duration": "10.0", "streams": ["video", "audio"]}
    )


def test_review_import_is_read_only_app_and_idempotent(tmp_path: Path) -> None:
    run_root, comfy_root = _fixture_run(tmp_path)
    first = import_comfy_review(run_root, comfy_root)
    second = import_comfy_review(run_root, comfy_root)
    workflow = json.loads((run_root / "review_workflow.json").read_text())
    assert first["workflow_sha256"] == second["workflow_sha256"]
    assert workflow["extra"]["linearMode"] is False
    assert workflow["extra"]["storycanvas_review_layout"] == "media_dag"
    app_inputs = workflow["extra"]["linearData"]["inputs"]
    assert len(app_inputs) == 3
    assert all(value.endswith(":text") and label == "text" for value, label in app_inputs)
    assert workflow["extra"]["storycanvas"]["network_calls"] is False
    assert {node["type"] for node in workflow["nodes"]} == {
        "StoryCanvasTextPreview",
        "StoryCanvasImagePreview",
        "StoryCanvasVideoPreview",
    }
    serialized = json.dumps(workflow)
    assert str(run_root) not in serialized
    assert "MINIMAX_H3_API_KEY" not in serialized
    assert second["counts"]["generated_images"] == 2
    assert all(item["transfer_mode"].startswith("existing_") for item in second["media"])


def test_review_import_rejects_sha_mismatch(tmp_path: Path) -> None:
    run_root, comfy_root = _fixture_run(tmp_path)
    manifest_path = run_root / "run_manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["artifacts"][0]["sha256"] = "0" * 64
    atomic_write_json(manifest_path, payload)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        import_comfy_review(run_root, comfy_root)


def test_review_import_rejects_path_outside_run(tmp_path: Path) -> None:
    run_root, comfy_root = _fixture_run(tmp_path)
    outside = tmp_path / "outside.png"
    _image(outside, (1, 2, 3))
    manifest_path = run_root / "run_manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["artifacts"][0]["path"] = str(outside)
    payload["artifacts"][0]["sha256"] = sha256_file(outside)
    atomic_write_json(manifest_path, payload)
    with pytest.raises(ValueError, match="outside the recorded Run root"):
        import_comfy_review(run_root, comfy_root)


def test_review_import_rejects_missing_midframe(tmp_path: Path) -> None:
    run_root, comfy_root = _fixture_run(tmp_path)
    next((run_root / "midframes").glob("*.png")).unlink()
    with pytest.raises(ValueError, match="Missing fixed 5-second midframe"):
        import_comfy_review(run_root, comfy_root)


def test_review_import_falls_back_to_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_root, comfy_root = _fixture_run(tmp_path)

    def fail_link(source: Path, target: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(review.os, "link", fail_link)
    report = import_comfy_review(run_root, comfy_root)
    assert {item["transfer_mode"] for item in report["media"]} == {"copy"}
