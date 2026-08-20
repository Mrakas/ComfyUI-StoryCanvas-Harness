from __future__ import annotations

import json
from pathlib import Path

import typer

from .api import create_app
from .audit import render_audit
from .engine import StoryCanvas
from .schemas import CanvasPlan, ExecutionMode, ExecutionPolicy, RunManifest, ShotInput, StoryInput
from .utils import atomic_write_json

app = typer.Typer(no_args_is_help=True, help="Build and execute auditable StoryCanvas workflows.")


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
) -> None:
    """Ask the typed Director for a CanvasPlan; no image or video calls are made."""
    if kind not in {"shot", "story"}:
        raise typer.BadParameter("--kind must be shot or story")
    policy = ExecutionPolicy(max_shots=max_shots)
    canvas = StoryCanvas.from_environment()
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
) -> None:
    """Compile a validated CanvasPlan into ComfyUI UI and API workflows."""
    parsed = CanvasPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    compiled = StoryCanvas.from_environment().compile_workflow(parsed, ExecutionPolicy())
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
    canvas = StoryCanvas.from_environment()
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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8189),
) -> None:
    """Start the standalone StoryCanvas REST service."""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


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


if __name__ == "__main__":  # pragma: no cover
    app()
