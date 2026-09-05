from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
from click import unstyle
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from storycanvas_harness.api import create_app
from storycanvas_harness.canvas_export import export_story_canvas
from storycanvas_harness.cli import app
from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.errors import PolicyViolation, ProviderError, ResumeConflict
from storycanvas_harness.onboarding import diagnose, run_demo
from storycanvas_harness.schemas import (
    CanvasPlan,
    ExecutionMode,
    ExecutionPolicy,
    RunManifest,
    ShotInput,
    StoryInput,
)
from storycanvas_harness.service import RunRequest, StoryCanvasService
from storycanvas_harness.storage import append_jsonl, read_jsonl
from storycanvas_harness.utils import atomic_write_json, sha256_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_asset",
        "keyframe_collision",
        "cycle",
        "unknown_dependency",
        "reference_dependency",
        "reference_order",
        "previous_shot",
    ],
)
def test_graph_rejects_invalid_dependencies(mock_canvas: StoryCanvas, problem: str) -> None:
    plan = mock_canvas.plan(StoryInput(free_text="A fox arrives.\nThe fox rests."))
    data = plan.model_dump(mode="json")
    if problem == "duplicate_asset":
        data["shared_assets"].append(data["shared_assets"][0])
    elif problem == "keyframe_collision":
        data["shots"][0]["keyframe_asset_id"] = data["shared_assets"][0]["asset_id"]
    elif problem == "cycle":
        data["shared_assets"][0]["dependencies"] = [data["shared_assets"][0]["asset_id"]]
    elif problem == "unknown_dependency":
        data["shared_assets"][0]["dependencies"] = ["missing"]
    elif problem == "reference_dependency":
        data["shots"][0]["dependencies"] = []
    elif problem == "reference_order":
        data["shots"][0]["references"][0]["order"] = 2
    else:
        data["shots"][0]["previous_shot_id"] = data["shots"][1]["shot_id"]
    with pytest.raises(ValueError):
        CanvasPlan.model_validate(data)


def test_graph_normalizes_shot_order(mock_canvas: StoryCanvas) -> None:
    plan = mock_canvas.plan(StoryInput(free_text="A fox arrives.\nThe fox rests."))
    data = plan.model_dump(mode="json")
    data["shots"].reverse()
    parsed = CanvasPlan.model_validate(data)
    assert [shot.order for shot in parsed.shots] == [1, 2]
    assert (
        mock_canvas.compile_workflow(parsed).workflow_sha256
        == mock_canvas.compile_workflow(plan).workflow_sha256
    )


def test_budget_uses_graph_not_reported_estimate(mock_canvas: StoryCanvas) -> None:
    plan = mock_canvas.plan(ShotInput(prompt="A paper fox rests."))
    plan.call_estimate.image_generation_calls = 0
    with pytest.raises(PolicyViolation, match="2 image calls"):
        mock_canvas.run_plan(plan, ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=0))
    assert not mock_canvas.runs_dir.exists()


@pytest.mark.parametrize("structured", [True, False])
def test_shot_limit_never_silently_truncates(mock_canvas: StoryCanvas, structured: bool) -> None:
    story = (
        StoryInput(shots=[ShotInput(prompt="First."), ShotInput(prompt="Second.")])
        if structured
        else StoryInput(free_text="First.\nSecond.")
    )
    with pytest.raises(PolicyViolation, match="2 shots"):
        mock_canvas.plan(story, ExecutionPolicy(max_shots=1))


def test_plan_and_execution_identity_include_content_and_provider(mock_canvas: StoryCanvas) -> None:
    value = ShotInput(prompt="A paper fox rests.")
    first = mock_canvas.plan(value)
    second = mock_canvas.plan(value)
    assert first.plan_id == second.plan_id
    second.shots[0].image_prompt += " A blue light."
    assert first.plan_id == second.plan_id
    policy = ExecutionPolicy()
    assert mock_canvas.execution_sha256(first, policy) != mock_canvas.execution_sha256(
        second, policy
    )
    before = mock_canvas.execution_sha256(first, policy)
    assert mock_canvas.image_provider is not None
    mock_canvas.image_provider.model = "different-model"
    assert before != mock_canvas.execution_sha256(first, policy)


def test_two_service_instances_claim_one_job(
    mock_canvas: StoryCanvas, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = Event()
    release = Event()
    counter_lock = Lock()
    calls = 0
    original = mock_canvas.run_plan

    def run_once(plan: CanvasPlan, policy: ExecutionPolicy):
        nonlocal calls
        with counter_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=10)
        return original(plan, policy)

    monkeypatch.setattr(mock_canvas, "run_plan", run_once)
    services = [StoryCanvasService(tmp_path / "service", mock_canvas) for _ in range(2)]
    plan = mock_canvas.plan(ShotInput(prompt="A fictional paper fox rests."))
    request = RunRequest(plan=plan, policy=ExecutionPolicy())
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(lambda index: services[index % 2].start_run(request), range(8)))
        assert started.wait(timeout=5)
        assert calls == 1
        assert len({state["job_id"] for state in states}) == 1
        assert services[1].get_run(states[0]["job_id"])["status"] in {"queued", "running"}
    finally:
        release.set()
        for service in services:
            service.close()
    assert services[0].get_run(states[0]["job_id"])["status"] == "complete"
    assert len(services[0].events(states[0]["job_id"])) == 2


def test_different_plans_with_same_id_create_different_jobs(
    mock_canvas: StoryCanvas, tmp_path: Path
) -> None:
    service = StoryCanvasService(tmp_path / "service", mock_canvas)
    plan = mock_canvas.plan(ShotInput(prompt="A paper fox rests."))
    changed = plan.model_copy(deep=True)
    changed.shots[0].image_prompt += " A different light."
    try:
        one = service.start_run(RunRequest(plan=plan, policy=ExecutionPolicy()))
        two = service.start_run(RunRequest(plan=changed, policy=ExecutionPolicy()))
        assert one["job_id"] != two["job_id"]
    finally:
        service.close()


def test_orphan_job_is_marked_failed_without_automatic_execution(
    mock_canvas: StoryCanvas, tmp_path: Path
) -> None:
    service = StoryCanvasService(tmp_path / "service", mock_canvas)
    atomic_write_json(
        service.job_dir / "job-orphan.json", {"job_id": "job-orphan", "status": "running"}
    )
    try:
        state = service.get_run("job-orphan")
        assert state["status"] == "failed"
        assert state["error_type"] == "InterruptedRun"
        assert not mock_canvas.runs_dir.exists()
    finally:
        service.close()


def test_api_errors_include_events_and_conflicting_inputs(
    mock_canvas: StoryCanvas, tmp_path: Path
) -> None:
    with TestClient(create_app(StoryCanvasService(tmp_path / "service", mock_canvas))) as client:
        assert client.get("/storycanvas/v1/runs/unknown/events").status_code == 404
        assert client.get("/storycanvas/v1/runs/bad$id/events").status_code == 400
        assert (
            client.post(
                "/storycanvas/v1/plans", content="{", headers={"content-type": "application/json"}
            ).status_code
            == 400
        )
        plan = mock_canvas.plan(ShotInput(prompt="A paper fox rests."))
        assert (
            client.post(
                "/storycanvas/v1/workflows",
                json={"plan_id": plan.plan_id, "plan": plan.model_dump(mode="json")},
            ).status_code
            == 400
        )


def test_help_and_health_do_not_initialize_providers_or_write_files(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "STORYCANVAS_PROVIDER_MODE": "codex",
        "STORYCANVAS_CODEX_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-m", "storycanvas_harness.cli", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "doctor" in result.stdout
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from storycanvas_harness.api import app; from fastapi.testclient import TestClient; assert TestClient(app).get('/health').status_code == 200",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


def test_doctor_is_local_and_profile_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORYCANVAS_PROVIDER_MODE", "invalid")
    monkeypatch.setattr(
        "storycanvas_harness.plugins.api.ServicePlugin.start",
        lambda *args: pytest.fail("doctor must not start plugins"),
    )
    report = diagnose(profile_file=ROOT / "profiles/basic.json", runs_dir=tmp_path / "not-created")
    assert report["ok"] is True
    assert report["network_calls"] == 0
    assert not (tmp_path / "not-created").exists()


def test_cli_bad_inputs_fail_before_provider_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORYCANVAS_PROVIDER_MODE", "codex")
    monkeypatch.setenv("STORYCANVAS_CODEX_ENABLED", "false")
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--kind", "typo", "--prompt", "A fox."])
    assert result.exit_code != 0
    assert "--kind must be" in unstyle(result.output)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    result = runner.invoke(app, ["run", "--input", str(invalid)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_packaged_demo_runs_offline_and_preserves_other_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORYCANVAS_PROVIDER_MODE", "codex")
    monkeypatch.setenv("STORYCANVAS_CODEX_ENABLED", "false")
    monkeypatch.setattr(
        "httpx.Client.send", lambda *args, **kwargs: pytest.fail("Demo must stay offline")
    )
    keep = tmp_path / "keep.txt"
    keep.write_text("preserve me")
    first = run_demo(tmp_path)
    second = run_demo(tmp_path)
    assert keep.read_text() == "preserve me"
    assert first["run_id"] == second["run_id"]
    assert first["media"] == 4
    assert second["call_counts"]["image_generation"] == 0
    assert second["call_counts"]["image_cache_hits"] == 4
    assert Path(first["viewer"]).is_file()


def test_relative_and_legacy_paths_export_and_continue(
    mock_canvas: StoryCanvas, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mock_canvas.runs_dir = Path("runs").resolve()
    record = mock_canvas.run(
        ShotInput(prompt="A paper fox rests."),
        ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=2),
    )
    relative_root = record.root.relative_to(tmp_path)
    manifest = record.manifest.model_copy(deep=True)
    manifest.run_root = str(relative_root)
    for artifact in manifest.artifacts:
        artifact.path = str(Path(artifact.path).relative_to(tmp_path))
    atomic_write_json(record.root / "run_manifest.json", manifest)
    assert export_story_canvas(relative_root, tmp_path / "viewer")["counts"]["media"] == 2
    if not __import__("shutil").which("ffmpeg"):
        return
    mock_canvas.image_provider = None
    continued = mock_canvas.complete_videos(
        relative_root,
        ExecutionPolicy(
            mode=ExecutionMode.FULL, allow_paid_video=True, max_image_calls=0, max_video_calls=1
        ),
    )
    assert continued.manifest.status.value == "complete"
    assert [
        artifact.sha256 for artifact in continued.manifest.artifacts if artifact.kind == "image"
    ] == [artifact.sha256 for artifact in record.manifest.artifacts]


def test_continuation_rejects_changed_plan(mock_canvas: StoryCanvas) -> None:
    record = mock_canvas.run(
        ShotInput(prompt="A paper fox rests."),
        ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=2),
    )
    record.plan.shots[0].image_prompt += " A changed instruction."
    atomic_write_json(record.root / "canvas_plan.json", record.plan)
    with pytest.raises(ResumeConflict, match="Plan content changed"):
        mock_canvas.complete_videos(
            record.root,
            ExecutionPolicy(mode=ExecutionMode.FULL, allow_paid_video=True, max_video_calls=1),
        )


def test_failure_persists_terminal_manifest_and_counts_attempts(
    mock_canvas: StoryCanvas, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "private-test-key-123456"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    assert mock_canvas.image_provider is not None

    def fail(*args):
        raise ProviderError(f"Provider rejected Bearer {secret}")

    monkeypatch.setattr(mock_canvas.image_provider, "generate", fail)
    record = mock_canvas.run(
        ShotInput(prompt="A paper fox rests."),
        ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=2),
    )
    persisted = RunManifest.model_validate_json((record.root / "run_manifest.json").read_text())
    assert persisted.status.value == "partial"
    assert persisted.finished_at is not None
    assert persisted.call_counts["image_generation"] == 1
    assert persisted.call_counts["image_successes"] == 0
    assert secret not in json.dumps(persisted.model_dump(mode="json"))


def test_plan_only_skips_evaluator(mock_canvas: StoryCanvas) -> None:
    class NoEvaluation:
        name = "never"
        model = "test"

        def evaluate(self, *args):
            pytest.fail("Plan-only must not execute evaluation plugins")

    mock_canvas.evaluator = NoEvaluation()
    record = mock_canvas.run(ShotInput(prompt="A paper fox rests."))
    assert not record.manifest.artifacts


def test_journal_concurrent_append_and_interrupted_tail(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: append_jsonl(path, {"value": value}), range(40)))
    assert {row["value"] for row in read_jsonl(path)} == set(range(40))
    with path.open("ab") as handle:
        handle.write(b'{"interrupted":')
    assert len(read_jsonl(path)) == 40
    append_jsonl(path, {"value": 40})
    assert len(read_jsonl(path)) == 41
    assert path.with_suffix(".jsonl.interrupted").read_bytes() == b'{"interrupted":\n'


def test_viewer_escapes_title_and_redacts_secrets(
    mock_canvas: StoryCanvas, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "unusual-private-key-123456"
    monkeypatch.setenv("CUSTOM_API_KEY", secret)
    record = mock_canvas.run(
        ShotInput(
            prompt="A paper fox rests.",
            title=f"</title><script>window.injected=true</script>{secret}",
        )
    )
    out = tmp_path / "canvas"
    export_story_canvas(record.root, out)
    html = (out / "index.html").read_text()
    assert "<script>window.injected=true</script>" not in html
    assert secret not in (out / "canvas_graph.json").read_text()
    assert secret not in html
    assert sha256_file(out / "index.html")


def test_compiler_resolves_custom_keyframe_ids(mock_canvas: StoryCanvas) -> None:
    plan = mock_canvas.plan(StoryInput(free_text="A fox arrives.\nThe fox rests."))
    data = plan.model_dump(mode="json")
    original_id = data["shots"][0]["keyframe_asset_id"]
    data["shots"][0]["keyframe_asset_id"] = "custom-frame"
    for shot in data["shots"]:
        shot["dependencies"] = [
            "custom-frame" if item == original_id else item for item in shot["dependencies"]
        ]
        for reference in shot["references"]:
            if reference.get("source_asset_id") == original_id:
                reference["source_asset_id"] = "custom-frame"
    compiled = mock_canvas.compile_workflow(CanvasPlan.model_validate(data))
    assert compiled.workflow and compiled.api_workflow


def test_compiler_rejects_unwired_shared_dependencies(mock_canvas: StoryCanvas) -> None:
    from storycanvas_harness.errors import WorkflowCompileError

    plan = mock_canvas.plan(ShotInput(prompt="A fox rests."))
    data = plan.model_dump(mode="json")
    extra = dict(data["shared_assets"][0])
    extra["asset_id"] = "dependent-style"
    extra["dependencies"] = [data["shared_assets"][0]["asset_id"]]
    data["shared_assets"].append(extra)
    with pytest.raises(WorkflowCompileError, match="cannot wire"):
        mock_canvas.compile_workflow(CanvasPlan.model_validate(data))


def test_comfy_budget_checked_before_provider_call(
    mock_canvas: StoryCanvas, monkeypatch: pytest.MonkeyPatch
) -> None:
    import storycanvas_harness.comfy_nodes as nodes

    monkeypatch.setattr(nodes, "_engine", lambda: mock_canvas)
    plan = mock_canvas.plan(ShotInput(prompt="A fox rests."))
    policy = ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=0)
    with pytest.raises(PolicyViolation, match="2 image calls"):
        nodes.StoryCanvasSharedVisualAssetNode().resolve(
            plan.model_dump(mode="json"),
            policy.model_dump(mode="json"),
            plan.shared_assets[0].asset_id,
        )
    assert not mock_canvas.runs_dir.exists()


def test_failed_cli_run_returns_json_and_nonzero(
    mock_canvas: StoryCanvas, monkeypatch: pytest.MonkeyPatch
) -> None:
    import storycanvas_harness.cli as cli

    def fail(*args, **kwargs):
        raise ProviderError("The image service is unavailable")

    monkeypatch.setattr(mock_canvas.image_provider, "generate", fail)
    monkeypatch.setattr(cli, "_canvas", lambda *args, **kwargs: mock_canvas)
    result = CliRunner().invoke(
        app, ["run", "--prompt", "A fox rests.", "--mode", "assets", "--json"]
    )
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] in {"failed", "partial"} and report["errors"]
    assert Path(report["manifest"]).is_file()


def test_separate_processes_reuse_one_generation(tmp_path: Path) -> None:
    """The on-disk claim must work beyond threads sharing a Python object."""
    child = """
import sys
from pathlib import Path
from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.providers.image import MockImageProvider
from storycanvas_harness.schemas import ExecutionPolicy, ExecutionMode, ShotInput
from storycanvas_harness.storage import append_jsonl
root = Path(sys.argv[1])
class CountingImage(MockImageProvider):
    def generate(self, *args, **kwargs):
        append_jsonl(root / "calls.jsonl", {"generated": True})
        return super().generate(*args, **kwargs)
canvas = StoryCanvas(runs_dir=root / "runs", image_provider=CountingImage())
record = canvas.run(ShotInput(prompt="A paper fox rests."), ExecutionPolicy(mode=ExecutionMode.ASSETS))
assert record.manifest.status.value == "complete"
print(record.run_id)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(tmp_path)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        results = [process.communicate(timeout=30) for process in children]
        for process, (_, stderr) in zip(children, results, strict=True):
            assert process.returncode == 0, stderr
        assert results[0][0] == results[1][0]
        assert len(read_jsonl(tmp_path / "calls.jsonl")) == 2
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.communicate()


def test_planning_count_distinguishes_run_from_precomputed_plan(mock_canvas: StoryCanvas) -> None:
    record = mock_canvas.run(ShotInput(prompt="A fox rests."))
    persisted = RunManifest.model_validate_json((record.root / "run_manifest.json").read_text())
    assert persisted.call_counts["planning"] == 1
    precomputed = mock_canvas.run_plan(record.plan, ExecutionPolicy())
    assert precomputed.manifest.call_counts["planning"] == 0
