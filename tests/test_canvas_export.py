from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from storycanvas_harness import export_story_canvas
from storycanvas_harness.canvas_export import CanvasMedia
from storycanvas_harness.cli import app
from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.schemas import ExecutionMode, ExecutionPolicy, ShotInput, StoryInput
from storycanvas_harness.utils import atomic_write_json, sha256_file


def _completed_run(mock_canvas: StoryCanvas) -> Path:
    record = mock_canvas.run(
        StoryInput(
            title="Moon garden",
            free_text="A botanist grows a luminous vine across three continuous shots.",
            shots=[
                ShotInput(prompt="Plant a silver seed."),
                ShotInput(prompt="The same seed becomes a blue vine."),
                ShotInput(prompt="The same vine releases three glowing moths."),
            ],
        ),
        ExecutionPolicy(
            mode=ExecutionMode.FULL,
            allow_paid_video=True,
            max_shots=3,
            max_image_calls=4,
            max_video_calls=3,
            max_concurrency=2,
        ),
    )
    return record.root


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_canvas_export_is_standalone_sanitized_and_deterministic(
    mock_canvas: StoryCanvas, tmp_path: Path
) -> None:
    run_root = _completed_run(mock_canvas)
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["errors"] = [
        {
            "message": f"retry under {run_root} with task_id=task-secret",
            "provider_request_id": "request-secret",
        }
    ]
    atomic_write_json(manifest_path, manifest)
    plan_path = run_root / "canvas_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["planning_provider"]["endpoint_kind"] = (
        "chatgpt-login;ephemeral;reasoning=medium;"
        "thread=01a023cc-234c-73f3-9547-18ec8c3e02d6;"
        "turn=01a023cc-23db-7362-98ec-2c7d62a85e31"
    )
    atomic_write_json(plan_path, plan)
    output = tmp_path / "canvas"

    first = export_story_canvas(run_root, output)
    graph_sha = sha256_file(output / "canvas_graph.json")
    index_sha = sha256_file(output / "index.html")
    second = export_story_canvas(run_root, output)

    assert first == second
    assert graph_sha == sha256_file(output / "canvas_graph.json")
    assert index_sha == sha256_file(output / "index.html")
    assert set(path.name for path in output.iterdir()) == {
        "canvas_graph.json",
        "export_report.json",
        "index.html",
        "media",
    }
    graph = json.loads((output / "canvas_graph.json").read_text(encoding="utf-8"))
    assert graph["schema_version"] == "storycanvas/canvas/v1"
    assert len(graph["stages"]) == 6
    assert sum(node["kind"] == "video" for node in graph["nodes"]) == 3
    assert sum(node["kind"] == "image" for node in graph["nodes"]) == 3
    rendered = json.dumps(graph)
    assert str(run_root) not in rendered
    assert str(Path.home()) not in rendered
    assert "task-secret" not in rendered
    assert "request-secret" not in rendered
    assert "provider_request_id" not in rendered
    assert "01a023cc-234c-73f3-9547-18ec8c3e02d6" not in rendered
    assert "01a023cc-23db-7362-98ec-2c7d62a85e31" not in rendered
    assert "chatgpt-login;ephemeral;reasoning=medium" in rendered
    assert first["network_calls"] is False
    assert all(not Path(item["path"]).is_absolute() for item in first["media"])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_canvas_export_rejects_sha_mismatch(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    run_root = _completed_run(mock_canvas)
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = "0" * 64
    atomic_write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        export_story_canvas(run_root, tmp_path / "canvas")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_canvas_export_rejects_path_outside_run(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    run_root = _completed_run(mock_canvas)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = str(outside)
    manifest["artifacts"][0]["sha256"] = sha256_file(outside)
    atomic_write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="outside the Run directory"):
        export_story_canvas(run_root, tmp_path / "canvas")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_canvas_export_cli(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    run_root = _completed_run(mock_canvas)
    output = tmp_path / "canvas-cli"
    result = CliRunner().invoke(
        app,
        ["canvas-export", "--run-dir", str(run_root), "--output-dir", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert "Standalone Canvas exported" in result.output
    assert (output / "index.html").is_file()


def test_canvas_media_schema_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="traversal-free"):
        CanvasMedia(path="../secret.png", kind="image", role="reference", sha256="0" * 64)
