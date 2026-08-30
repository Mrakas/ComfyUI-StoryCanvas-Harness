from __future__ import annotations

import json
from pathlib import Path

import typer

from .api import create_app
from .audit import render_audit
from .canvas_export import export_story_canvas
from .engine import StoryCanvas
from .review import import_comfy_review
from .schemas import CanvasPlan, ExecutionMode, ExecutionPolicy, RunManifest, ShotInput, StoryInput
from .utils import atomic_write_json, atomic_write_text

app = typer.Typer(no_args_is_help=True, help="Build and execute auditable StoryCanvas workflows.")


def _canvas(profile_file: Path | None = None, *, runs_dir: Path | None = None) -> StoryCanvas:
    return (
        StoryCanvas.from_profile(profile_file, runs_dir=runs_dir)
        if profile_file is not None
        else StoryCanvas.from_environment(runs_dir=runs_dir)
    )


def _policy(
    mode: ExecutionMode,
    allow_paid_video: bool,
    max_shots: int,
    max_search_calls: int,
    max_image_calls: int,
    max_video_calls: int,
    max_concurrency: int,
) -> ExecutionPolicy:
    return ExecutionPolicy(
        mode=mode,
        allow_paid_video=allow_paid_video,
        max_shots=max_shots,
        max_search_calls=max_search_calls,
        max_image_calls=max_image_calls,
        max_video_calls=max_video_calls,
        max_concurrency=max_concurrency,
    )


def _read_input(kind: str, prompt: str | None, input_file: Path | None) -> ShotInput | StoryInput:
    if input_file:
        payload = json.loads(input_file.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
            wrapped_kind = payload.get("input_kind")
            if wrapped_kind in {"shot", "story"} and wrapped_kind != kind:
                raise typer.BadParameter(
                    f"--kind={kind} does not match input_kind={wrapped_kind} in {input_file}"
                )
            payload = payload["payload"]
        return (
            ShotInput.model_validate(payload)
            if kind == "shot"
            else StoryInput.model_validate(payload)
        )
    if not prompt:
        raise typer.BadParameter("Provide --prompt or --input")
    return ShotInput(prompt=prompt) if kind == "shot" else StoryInput(free_text=prompt)


@app.command()
def plan(
    kind: str = typer.Option("shot", help="shot or story"),
    prompt: str | None = typer.Option(None, help="Free-text shot/story input"),
    input_file: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False),
    output: Path = typer.Option(Path("canvas_plan.json")),
    max_shots: int = typer.Option(12),
    profile_file: Path | None = typer.Option(
        None, "--profile", exists=True, dir_okay=False, help="Plugin composition profile"
    ),
) -> None:
    """Ask the typed Director for a CanvasPlan; no image or video calls are made."""
    if kind not in {"shot", "story"}:
        raise typer.BadParameter("--kind must be shot or story")
    policy = ExecutionPolicy(max_shots=max_shots)
    with _canvas(profile_file) as canvas:
        result = canvas.plan(_read_input(kind, prompt, input_file), policy)
    atomic_write_json(output, result)
    typer.echo(f"Plan: {result.plan_id} · {len(result.shots)} shot(s) · {output}")
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")


@app.command("compile")
def compile_command(
    plan_file: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = typer.Option(Path("workflow.json")),
    api_output: Path = typer.Option(Path("workflow_api.json")),
    profile_file: Path | None = typer.Option(
        None, "--profile", exists=True, dir_okay=False, help="Plugin composition profile"
    ),
) -> None:
    """Compile a validated CanvasPlan into ComfyUI UI and API workflows."""
    parsed = CanvasPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    with _canvas(profile_file) as canvas:
        compiled = canvas.compile_workflow(parsed, ExecutionPolicy())
    atomic_write_json(output, compiled.workflow)
    atomic_write_json(api_output, compiled.api_workflow)
    typer.echo(f"Workflow SHA256: {compiled.workflow_sha256}")
    typer.echo(f"UI: {output}\nAPI: {api_output}")


@app.command()
def run(
    plan_file: Path | None = typer.Option(None, "--plan", exists=True, dir_okay=False),
    kind: str = typer.Option("shot"),
    prompt: str | None = typer.Option(None),
    input_file: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False),
    mode: ExecutionMode = typer.Option(ExecutionMode.PLAN_ONLY),
    allow_paid_video: bool = typer.Option(False),
    max_shots: int = typer.Option(12),
    max_search_calls: int = typer.Option(8),
    max_image_calls: int = typer.Option(16),
    max_video_calls: int = typer.Option(0),
    max_concurrency: int = typer.Option(4),
    profile_file: Path | None = typer.Option(
        None, "--profile", exists=True, dir_okay=False, help="Plugin composition profile"
    ),
) -> None:
    """Execute a plan under explicit search, image, video, and concurrency limits."""
    policy = _policy(
        mode,
        allow_paid_video,
        max_shots,
        max_search_calls,
        max_image_calls,
        max_video_calls,
        max_concurrency,
    )
    with _canvas(profile_file) as canvas:
        record = (
            canvas.run_plan(
                CanvasPlan.model_validate_json(plan_file.read_text(encoding="utf-8")), policy
            )
            if plan_file
            else canvas.run(_read_input(kind, prompt, input_file), policy)
        )
    typer.echo(
        f"{record.manifest.status.value}: {record.run_id}\n"
        f"Manifest: {record.root / 'run_manifest.json'}\nAudit: {record.root / 'audit.html'}"
    )


@app.command("profile-inspect")
def profile_inspect(
    profile_file: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path | None = typer.Option(None, help="Optional JSON inspection report"),
) -> None:
    """Validate a Profile, activate its plugins, and print capability bindings."""
    from .plugins import build_builtin_registry, load_profile

    profile = load_profile(profile_file)
    registry = build_builtin_registry(profile, root=Path.cwd())
    try:
        report = {
            "schema_version": profile.schema_version,
            "name": profile.name,
            "description": profile.description,
            "profile_sha256": profile.sha256,
            "composition_sha256": registry.composition_sha256,
            "bindings": profile.bindings,
            "allow_permissions": profile.allow_permissions,
            "plugins": [item.model_dump(mode="json") for item in registry.snapshots()],
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if output is not None:
            atomic_write_text(output, rendered)
        typer.echo(rendered.rstrip())
    finally:
        registry.dispose_all()


@app.command("pack-inspect")
def pack_inspect(
    pack_file: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path | None = typer.Option(None, help="Optional JSON inspection report"),
) -> None:
    """Validate a Pack and its referenced Profile, Plugins, and Skill files."""
    from .plugins import load_pack, load_profile

    pack = load_pack(pack_file)
    profile_path = (pack_file.parent / pack.profile).resolve()
    if not profile_path.is_file():
        raise typer.BadParameter(f"Pack Profile does not exist: {profile_path}")
    profile = load_profile(profile_path)
    if pack.plugins != profile.plugins:
        raise typer.BadParameter("Pack Plugin list must exactly match its referenced Profile")
    skill_paths = [(pack_file.parent / item).resolve() for item in pack.skills]
    missing_skills = [str(path) for path in skill_paths if not path.is_file()]
    if missing_skills:
        raise typer.BadParameter(f"Pack Skills do not exist: {missing_skills}")
    report = {
        "api_version": pack.api_version,
        "id": pack.id,
        "version": pack.version,
        "description": pack.description,
        "pack_sha256": pack.sha256,
        "profile": pack.profile,
        "profile_sha256": profile.sha256,
        "plugins": pack.plugins,
        "skills": pack.skills,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        atomic_write_text(output, rendered)
    typer.echo(rendered.rstrip())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8189),
) -> None:
    """Start the standalone StoryCanvas REST service."""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


@app.command("complete-videos")
def complete_videos(
    run_root: Path = typer.Argument(..., exists=True, file_okay=False),
    allow_paid_video: bool = typer.Option(False, help="Required paid-video confirmation"),
    max_video_calls: int = typer.Option(0),
    max_concurrency: int = typer.Option(1),
    profile_file: Path | None = typer.Option(
        None, "--profile", exists=True, dir_okay=False, help="Plugin composition profile"
    ),
) -> None:
    """Continue a validated assets run with paid video calls, without regenerating images."""
    if not allow_paid_video:
        raise typer.BadParameter("Pass --allow-paid-video to confirm paid video generation")
    policy = _policy(
        ExecutionMode.FULL,
        allow_paid_video,
        max_shots=12,
        max_search_calls=0,
        max_image_calls=16,
        max_video_calls=max_video_calls,
        max_concurrency=max_concurrency,
    )
    with _canvas(profile_file, runs_dir=run_root.parent) as canvas:
        record = canvas.complete_videos(run_root, policy)
    typer.echo(
        f"{record.manifest.status.value}: {record.run_id}\n"
        f"Manifest: {record.root / 'run_manifest.json'}\nAudit: {record.root / 'audit.html'}"
    )


@app.command("export-html")
def export_html(
    plan_file: Path = typer.Option(..., "--plan", exists=True, dir_okay=False),
    manifest_file: Path = typer.Option(..., "--manifest", exists=True, dir_okay=False),
    output: Path = typer.Option(Path("audit.html")),
) -> None:
    """Render a lightweight, shareable audit page from local artifacts."""
    parsed_plan = CanvasPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    manifest = RunManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
    render_audit(parsed_plan, manifest, output)
    typer.echo(str(output))


@app.command("comfy-review")
def comfy_review(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False),
    comfy_root: Path = typer.Option(..., "--comfy-root", exists=True, file_okay=False),
) -> None:
    """Import a completed Run into a read-only ComfyUI App without provider calls."""
    report = import_comfy_review(run_dir, comfy_root)
    typer.echo(
        f"Read-only review imported: {report['run_id']}\n"
        f"App: {report['target']['app_workflow']}\n"
        f"Workflow: {report['target']['review_workflow']}\n"
        f"Media: {report['counts']['staged_unique_media']} unique local file(s)"
    )


@app.command("canvas-export")
def canvas_export(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., "--output-dir", file_okay=False),
) -> None:
    """Export a completed Run as a standalone, read-only media DAG viewer."""
    report = export_story_canvas(run_dir, output_dir)
    typer.echo(
        f"Standalone Canvas exported: {report['run_id']}\n"
        f"Viewer: {output_dir / 'index.html'}\n"
        f"Graph: {output_dir / 'canvas_graph.json'}\n"
        f"Media: {report['counts']['media']} verified local file(s)"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
