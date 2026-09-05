from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import ParamSpec, TypeVar

import typer

from .audit import render_audit
from .canvas_export import export_story_canvas
from .engine import StoryCanvas
from .errors import StoryCanvasError
from .onboarding import diagnose, run_demo
from .review import import_comfy_review
from .schemas import (
    CanvasPlan,
    ExecutionMode,
    ExecutionPolicy,
    RunManifest,
    RunRecord,
    ShotInput,
    StoryInput,
)
from .utils import atomic_write_json, atomic_write_text, safe_error

app = typer.Typer(no_args_is_help=True, help="Build and execute auditable StoryCanvas workflows.")


P = ParamSpec("P")
R = TypeVar("R")


def friendly_errors(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except (ValueError, OSError, StoryCanvasError) as error:
            typer.echo(f"Error: {safe_error(error)}", err=True)
            raise typer.Exit(code=1) from None

    return wrapped


def _canvas(
    profile_file: Path | None = None, *, runs_dir: Path | None = None, videos_only: bool = False
) -> StoryCanvas:
    return (
        StoryCanvas.from_profile(profile_file, runs_dir=runs_dir)
        if profile_file is not None
        else StoryCanvas.from_environment(runs_dir=runs_dir, videos_only=videos_only)
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


def _read_input(
    kind: str | None, prompt: str | None, input_file: Path | None
) -> ShotInput | StoryInput:
    if kind is not None and kind not in {"shot", "story"}:
        raise typer.BadParameter("--kind must be shot or story")
    if (prompt is None) == (input_file is None):
        raise typer.BadParameter("Provide exactly one of --prompt or --input")
    if input_file:
        payload = json.loads(input_file.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
            wrapped_kind = payload.get("input_kind")
            if wrapped_kind not in {"shot", "story"}:
                raise typer.BadParameter("Wrapped input_kind must be shot or story")
            if kind is not None and wrapped_kind != kind:
                raise typer.BadParameter(
                    f"--kind={kind} does not match input_kind={wrapped_kind} in {input_file}"
                )
            kind = wrapped_kind
            payload = payload["payload"]
        return (
            ShotInput.model_validate(payload)
            if kind in {None, "shot"}
            else StoryInput.model_validate(payload)
        )
    if not prompt:
        raise typer.BadParameter("Provide --prompt or --input")
    return ShotInput(prompt=prompt) if kind in {None, "shot"} else StoryInput(free_text=prompt)


@app.command()
@friendly_errors
def plan(
    kind: str | None = typer.Option(
        None, help="shot or story; inferred from wrapped JSON, otherwise shot"
    ),
    prompt: str | None = typer.Option(None, help="Free-text shot/story input"),
    input_file: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False),
    output: Path = typer.Option(Path("canvas_plan.json")),
    max_shots: int = typer.Option(12),
    profile_file: Path | None = typer.Option(
        None, "--profile", exists=True, dir_okay=False, help="Plugin composition profile"
    ),
) -> None:
    """Ask the typed Director for a CanvasPlan; no image or video calls are made."""
    value = _read_input(kind, prompt, input_file)
    policy = ExecutionPolicy(max_shots=max_shots)
    with _canvas(profile_file) as canvas:
        result = canvas.plan(value, policy)
    atomic_write_json(output, result)
    typer.echo(f"Plan: {result.plan_id} · {len(result.shots)} shot(s) · {output}")
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")


@app.command("compile")
@friendly_errors
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
    with StoryCanvas.from_profile(profile_file) if profile_file else StoryCanvas() as canvas:
        compiled = canvas.compile_workflow(parsed, ExecutionPolicy())
    atomic_write_json(output, compiled.workflow)
    atomic_write_json(api_output, compiled.api_workflow)
    typer.echo(f"Workflow SHA256: {compiled.workflow_sha256}")
    typer.echo(f"UI: {output}\nAPI: {api_output}")


@app.command()
@friendly_errors
def run(
    plan_file: Path | None = typer.Option(None, "--plan", exists=True, dir_okay=False),
    kind: str | None = typer.Option(
        None, help="shot or story; inferred from wrapped JSON, otherwise shot"
    ),
    prompt: str | None = typer.Option(None),
    input_file: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False),
    runs_dir: Path | None = typer.Option(None, "--runs-dir", file_okay=False),
    json_output: bool = typer.Option(False, "--json"),
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
    if plan_file and (prompt is not None or input_file is not None or kind is not None):
        raise typer.BadParameter("--plan cannot be combined with --prompt, --input, or --kind")
    value = (
        CanvasPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
        if plan_file
        else _read_input(kind, prompt, input_file)
    )
    policy = _policy(
        mode,
        allow_paid_video,
        max_shots,
        max_search_calls,
        max_image_calls,
        max_video_calls,
        max_concurrency,
    )
    with _canvas(profile_file, runs_dir=runs_dir) as canvas:
        record = (
            canvas.run_plan(value, policy)
            if isinstance(value, CanvasPlan)
            else canvas.run(value, policy)
        )
    _print_record(record, json_output)


def _print_record(record: RunRecord, json_output: bool) -> None:
    report = {
        "status": record.manifest.status.value,
        "run_id": record.run_id,
        "run_dir": str(record.root),
        "manifest": str(record.root / "run_manifest.json"),
        "audit": str(record.root / "audit.html"),
        "errors": record.manifest.errors,
    }
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        typer.echo(
            f"{report['status']}: {record.run_id}\nManifest: {report['manifest']}\nAudit: {report['audit']}"
        )
        for error in record.manifest.errors:
            typer.echo(f"Error: {safe_error(error.get('error', error))}", err=True)
    if record.manifest.status.value != "complete":
        raise typer.Exit(code=1)


@app.command("profile-inspect")
@friendly_errors
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
@friendly_errors
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
@friendly_errors
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8189),
) -> None:
    """Start the standalone StoryCanvas REST service."""
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command("complete-videos")
@friendly_errors
def complete_videos(
    run_root: Path = typer.Argument(..., exists=True, file_okay=False),
    json_output: bool = typer.Option(False, "--json"),
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
    parsed = CanvasPlan.model_validate_json(
        (run_root / "canvas_plan.json").read_text(encoding="utf-8")
    )
    policy = _policy(
        ExecutionMode.FULL,
        allow_paid_video,
        max_shots=len(parsed.shots),
        max_search_calls=0,
        max_image_calls=0,
        max_video_calls=max_video_calls,
        max_concurrency=max_concurrency,
    )
    with _canvas(profile_file, runs_dir=run_root.parent, videos_only=True) as canvas:
        record = canvas.complete_videos(run_root, policy)
    _print_record(record, json_output)


@app.command("export-html")
@friendly_errors
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
@friendly_errors
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
@friendly_errors
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


@app.command()
@friendly_errors
def doctor(
    mode: ExecutionMode = typer.Option(ExecutionMode.PLAN_ONLY),
    profile_file: Path | None = typer.Option(None, "--profile", exists=True, dir_okay=False),
    runs_dir: Path | None = typer.Option(None, "--runs-dir", file_okay=False),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Check local configuration and dependencies without calling providers."""
    report = diagnose(mode=mode, profile_file=profile_file, runs_dir=runs_dir)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            typer.echo(f"{item['status'].upper():7} {item['name']}: {item['message']}")
        typer.echo(
            "Local checks passed. No provider was contacted."
            if report["ok"]
            else "Fix the errors above, then run doctor again."
        )
    if not report["ok"]:
        raise typer.Exit(code=1)


@app.command()
@friendly_errors
def demo(
    output_dir: Path = typer.Option(Path("output/demo"), "--output-dir", file_okay=False),
    with_video: bool = typer.Option(False, "--with-video", help="Include local ffmpeg mock videos"),
    open_viewer: bool = typer.Option(
        False, "--open", help="Open the generated Canvas in your browser"
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run a three-shot local example without keys, network access, or a GPU."""
    report = run_demo(output_dir, with_video=with_video)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        typer.echo(
            f"Demo complete: {report['run_id']}\nCanvas: {report['viewer']}\nManifest: {report['manifest']}\nAudit: {report['audit']}\nNetwork calls: 0 · Paid calls: 0"
        )
    if open_viewer:
        import webbrowser

        if not webbrowser.open(Path(report["viewer"]).as_uri() + "?final=1"):
            typer.echo(f"Open this Canvas manually: {report['viewer']}", err=True)


if __name__ == "__main__":  # pragma: no cover
    app()
