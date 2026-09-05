from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment
from PIL import Image
from pydantic import Field, model_validator

from .media import full_decode, probe_media
from .schemas import ArtifactRecord, CanvasPlan, RunManifest, SafeId, StrictModel
from .storage import artifact_path
from .utils import atomic_write_json, atomic_write_text, ensure_safe_id, sha256_file, sha256_json

CANVAS_SCHEMA_VERSION: Literal["storycanvas/canvas/v1"] = "storycanvas/canvas/v1"
STAGES = [
    "Story Prompt",
    "Director & Plugins",
    "Persistent Media State",
    "Shot Prompts",
    "Canvas Keyframes",
    "Videos & Receipts",
]
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|mk)-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{10,}", re.IGNORECASE),
)
_TRACE_ID_PATTERN = re.compile(
    r"\b(?:task|thread|turn|item)(?:[_-]?id)?\s*[:=]\s*"
    r"[0-9a-f]{8,}(?:-[0-9a-f]{4,}){2,}\b",
    re.IGNORECASE,
)


class CanvasMedia(StrictModel):
    path: str
    kind: Literal["image", "video"]
    role: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_safe_relative_path(self) -> CanvasMedia:
        value = Path(self.path)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError(f"Canvas media path must be relative and traversal-free: {self.path}")
        return self


class CanvasNode(StrictModel):
    node_id: SafeId
    kind: Literal[
        "input",
        "director",
        "plugin",
        "state",
        "prompt",
        "image",
        "video",
        "receipt",
        "output",
    ]
    title: str
    summary: str
    stage: int = Field(ge=1, le=6)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    media: list[CanvasMedia] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class CanvasEdge(StrictModel):
    source: SafeId
    target: SafeId
    label: str
    stage: int = Field(ge=1, le=6)
    dashed: bool = False


class CanvasGraph(StrictModel):
    schema_version: Literal["storycanvas/canvas/v1"] = CANVAS_SCHEMA_VERSION
    run_id: SafeId
    plan_id: SafeId
    title: str
    subtitle: str
    stages: list[str]
    board: dict[str, int]
    nodes: list[CanvasNode]
    edges: list[CanvasEdge]
    graph_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_topology(self) -> CanvasGraph:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Canvas node IDs must be unique")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"Canvas edge references an unknown node: {edge}")
        if self.stages != STAGES:
            raise ValueError("Canvas stages do not match storycanvas/canvas/v1")
        if self.board.get("width", 0) <= 0 or self.board.get("height", 0) <= 0:
            raise ValueError("Canvas board dimensions must be positive")
        return self


def _inside(path: Path, root: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes the Run directory: {path}") from error
    return resolved


def _source_path(value: str, run_root: Path, recorded_root: Path | None) -> Path:
    return artifact_path(value, run_root, recorded_root)


def _redact_text(value: Any, roots: list[str]) -> str:
    from .utils import safe_error

    rendered = safe_error(value or "")
    for root in sorted({item for item in roots if item}, key=len, reverse=True):
        rendered = rendered.replace(root, "<RUN_DIR>")
    for pattern in _SECRET_PATTERNS:
        rendered = pattern.sub("[REDACTED]", rendered)
    rendered = _TRACE_ID_PATTERN.sub("", rendered)
    rendered = re.sub(r"\b(?:task|thread|turn|item)[_-]?id\s*[:=]\s*\S+", "", rendered, flags=re.I)
    rendered = re.sub(r";{2,}", ";", rendered)
    return rendered.strip(" ;")


def _redact_value(value: Any, roots: list[str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_value(item, roots)
            for key, item in value.items()
            if str(key).lower()
            not in {"task_id", "thread_id", "turn_id", "item_id", "provider_request_id"}
        }
    if isinstance(value, list):
        return [_redact_value(item, roots) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, roots) for item in value]
    if isinstance(value, str):
        return _redact_text(value, roots)
    return value


def _load_prompts(run_root: Path) -> dict[str, dict[str, Any]]:
    path = run_root / "prompts.jsonl"
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict) and value.get("asset_id"):
            rows[str(value["asset_id"])] = value
    return rows


class _MediaStager:
    def __init__(self, run_root: Path, output_root: Path, recorded_root: Path | None):
        self.run_root = run_root
        self.output_root = output_root
        self.recorded_root = recorded_root
        self.records: dict[str, dict[str, Any]] = {}
        self.validations: dict[str, dict[str, Any]] = {}

    def _validate(
        self, source: Path, kind: Literal["image", "video"], digest: str
    ) -> dict[str, Any]:
        cached = self.validations.get(digest)
        if cached is not None:
            return cached
        if kind == "image":
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                result = {
                    "format": image.format,
                    "width": image.width,
                    "height": image.height,
                }
        else:
            probe = probe_media(source)
            streams = probe.get("streams") or []
            video_stream = next(
                (stream for stream in streams if stream.get("codec_type") == "video"), None
            )
            if video_stream is None:
                raise ValueError(f"Video has no video stream: {source.name}")
            full_decode(source)
            result = {
                "codec": video_stream.get("codec_name"),
                "width": video_stream.get("width"),
                "height": video_stream.get("height"),
                "duration_seconds": (probe.get("format") or {}).get("duration"),
                "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
            }
        self.validations[digest] = result
        return result

    def add(self, value: str, *, kind: Literal["image", "video"], sha256: str) -> CanvasMedia:
        source = _source_path(value, self.run_root, self.recorded_root)
        actual = sha256_file(source)
        if actual != sha256:
            raise ValueError(f"SHA256 mismatch for {source.name}: expected {sha256}, got {actual}")
        validation = self._validate(source, kind, actual)
        suffix = source.suffix.lower() or (".png" if kind == "image" else ".mp4")
        relative = Path("media") / f"{actual}{suffix}"
        target = self.output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_file(target) != actual:
                raise ValueError(f"Existing staged media has the wrong SHA256: {relative}")
            same_inode = (
                source.stat().st_dev == target.stat().st_dev
                and source.stat().st_ino == target.stat().st_ino
            )
            transfer_mode = "hardlink" if same_inode else "copy"
        else:
            try:
                os.link(source, target)
                transfer_mode = "hardlink"
            except OSError:
                shutil.copy2(source, target)
                transfer_mode = "copy"
        self.records[relative.as_posix()] = {
            "path": relative.as_posix(),
            "kind": kind,
            "sha256": actual,
            "bytes": target.stat().st_size,
            "transfer_mode": transfer_mode,
            "validation": validation,
        }
        return CanvasMedia(path=relative.as_posix(), kind=kind, role="media", sha256=actual)


def _prompt_for(
    artifact: ArtifactRecord | None,
    prompt_record: dict[str, Any] | None,
    roots: list[str],
) -> tuple[str, str]:
    if artifact is None:
        return "N/A", "N/A"
    planned = (prompt_record or {}).get("planned_prompt") or artifact.prompt or "N/A"
    actual = (
        artifact.metadata.get("actual_prompt")
        or (prompt_record or {}).get("actual_prompt")
        or artifact.prompt
        or "N/A"
    )
    return _redact_text(planned, roots), _redact_text(actual, roots)


def _receipt_details(artifact: ArtifactRecord | None) -> dict[str, Any]:
    if artifact is None:
        return {"status": "missing"}
    receipt = artifact.receipt
    return {
        "artifact_id": artifact.artifact_id,
        "output_sha256": artifact.sha256,
        "provider": receipt.provider if receipt else None,
        "model": receipt.model if receipt else None,
        "operation": receipt.operation if receipt else None,
        "request_sha256": receipt.request_sha256 if receipt else None,
        "attempts": len(artifact.attempts),
    }


def _story_prompt(plan: CanvasPlan, roots: list[str]) -> str:
    source = plan.source_input
    value = source.get("free_text") or source.get("prompt")
    if not value:
        shot_prompts = [
            str(item.get("prompt"))
            for item in source.get("shots") or []
            if isinstance(item, dict) and item.get("prompt")
        ]
        value = (
            f"{plan.title}. "
            + " ".join(
                f"Shot {index}: {prompt}" for index, prompt in enumerate(shot_prompts, start=1)
            )
            if shot_prompts
            else plan.title
        )
    return _redact_text(value, roots)


def _graph_without_sha(graph: CanvasGraph) -> dict[str, Any]:
    value = graph.model_dump(mode="json", exclude_none=True)
    value.pop("graph_sha256", None)
    return value


def build_canvas_graph(
    plan: CanvasPlan,
    manifest: RunManifest,
    *,
    run_root: Path,
    output_root: Path,
) -> tuple[CanvasGraph, list[dict[str, Any]]]:
    ensure_safe_id(manifest.run_id, label="run_id")
    if manifest.plan_id != plan.plan_id:
        raise ValueError("Run manifest plan_id does not match canvas_plan.json")
    recorded_root = Path(manifest.run_root).expanduser() if manifest.run_root else None
    roots = [str(run_root), str(recorded_root or ""), str(Path.home())]
    artifacts = {item.artifact_id: item for item in manifest.artifacts}
    prompts = _load_prompts(run_root)
    stager = _MediaStager(run_root, output_root, recorded_root)
    nodes: list[CanvasNode] = []
    edges: list[CanvasEdge] = []

    def add_node(**kwargs: Any) -> CanvasNode:
        node = CanvasNode(**kwargs)
        nodes.append(node)
        return node

    def add_edge(source: str, target: str, label: str, stage: int, dashed: bool = False) -> None:
        edges.append(
            CanvasEdge(source=source, target=target, label=label, stage=stage, dashed=dashed)
        )

    add_node(
        node_id="story-input",
        kind="input",
        title="Story Prompt",
        summary=_story_prompt(plan, roots)[:180],
        stage=1,
        x=80,
        y=170,
        width=340,
        height=220,
        details={"prompt": _story_prompt(plan, roots), "input_sha256": plan.input_sha256},
    )
    add_node(
        node_id="director",
        kind="director",
        title="Typed Director",
        summary=f"{plan.planning_provider.model} · {len(plan.shots)} shot plan",
        stage=2,
        x=520,
        y=120,
        width=340,
        height=180,
        details={
            "provider": _redact_value(
                plan.planning_provider.model_dump(mode="json", exclude_none=True), roots
            ),
            "warnings": _redact_value(plan.warnings, roots),
            "call_estimate": plan.call_estimate.model_dump(mode="json"),
        },
    )
    add_node(
        node_id="active-profile",
        kind="plugin",
        title="Profile & Plugins",
        summary=(" · ".join(manifest.plugins) or "environment composition")[:180],
        stage=2,
        x=520,
        y=350,
        width=340,
        height=180,
        details={
            "plugins": manifest.plugins,
            "composition_sha256": manifest.composition_sha256,
        },
    )
    add_edge("story-input", "director", "plan", 2)
    add_edge("active-profile", "director", "compose", 2)

    style_artifact = artifacts.get("style-bible")
    style_media: list[CanvasMedia] = []
    if style_artifact is not None and style_artifact.kind == "image":
        item = stager.add(style_artifact.path, kind="image", sha256=style_artifact.sha256)
        style_media.append(item.model_copy(update={"role": "style_bible"}))
    style_planned, style_actual = _prompt_for(style_artifact, prompts.get("style-bible"), roots)
    add_node(
        node_id="visual-bible",
        kind="state",
        title="Visual Bible",
        summary=plan.visual_bible.style_prompt[:180],
        stage=3,
        x=960,
        y=90,
        width=380,
        height=300,
        media=style_media,
        details={
            "style_prompt": _redact_text(plan.visual_bible.style_prompt, roots),
            "planned_prompt": style_planned,
            "actual_prompt": style_actual,
            "continuity_rules": [
                _redact_text(item, roots) for item in plan.visual_bible.continuity_rules
            ],
            "receipt": _receipt_details(style_artifact),
        },
    )

    reference_media: dict[str, CanvasMedia] = {}
    reference_details: list[dict[str, Any]] = []
    reference_details_by_shot: dict[str, list[dict[str, Any]]] = {}
    for shot in plan.shots:
        artifact = artifacts.get(shot.keyframe_asset_id)
        shot_reference_details: list[dict[str, Any]] = []
        for reference in artifact.ordered_references if artifact else shot.references:
            if not reference.path or not reference.sha256:
                continue
            key = reference.sha256
            if key not in reference_media:
                item = stager.add(reference.path, kind="image", sha256=reference.sha256)
                reference_media[key] = item.model_copy(update={"role": reference.role})
            detail = {
                "shot_id": shot.shot_id,
                "order": reference.order,
                "role": reference.role,
                "provenance": reference.provenance.value,
                "sha256": reference.sha256,
            }
            reference_details.append(detail)
            shot_reference_details.append(detail)
        reference_details_by_shot[shot.shot_id] = shot_reference_details
    add_node(
        node_id="reference-state",
        kind="state",
        title="Ordered References",
        summary=f"{len(reference_media)} unique inputs with explicit roles",
        stage=3,
        x=960,
        y=450,
        width=380,
        height=280,
        media=list(reference_media.values()),
        details={"references": reference_details},
    )
    add_node(
        node_id="media-dag",
        kind="state",
        title="Persistent Media DAG",
        summary="Prompts · dependencies · artifacts · receipts",
        stage=3,
        x=960,
        y=790,
        width=380,
        height=190,
        details={
            "plan_id": plan.plan_id,
            "run_id": manifest.run_id,
            "status": manifest.status.value,
            "input_sha256": manifest.input_sha256,
        },
    )
    add_edge("director", "visual-bible", "externalize state", 3)
    add_edge("visual-bible", "reference-state", "style & identity", 3)
    add_edge("reference-state", "media-dag", "typed assets", 3)

    shot_start_x = 1480
    shot_gap = 520
    video_nodes: list[str] = []
    receipt_rows: list[dict[str, Any]] = []
    keyframe_node_by_shot: dict[str, str] = {}
    for index, shot in enumerate(plan.shots):
        x = shot_start_x + index * shot_gap
        public_shot_id = f"shot-{shot.order:04d}"
        shot_prompt_id = f"{public_shot_id}-prompt"
        keyframe_id = f"{public_shot_id}-keyframe"
        video_prompt_id = f"{public_shot_id}-video-prompt"
        video_id = f"{public_shot_id}-video"
        keyframe_node_by_shot[shot.shot_id] = keyframe_id
        keyframe_artifact = artifacts.get(shot.keyframe_asset_id)
        video_artifact = artifacts.get(f"{shot.shot_id}-video")
        planned, actual = _prompt_for(keyframe_artifact, prompts.get(shot.keyframe_asset_id), roots)
        add_node(
            node_id=shot_prompt_id,
            kind="prompt",
            title=f"Shot {shot.order} Prompt",
            summary=_redact_text(shot.original_prompt, roots)[:180],
            stage=4,
            x=x,
            y=90,
            width=420,
            height=260,
            details={
                "shot_id": shot.shot_id,
                "original_prompt": _redact_text(shot.original_prompt, roots),
                "previous_shot_id": shot.previous_shot_id,
                "dependencies": shot.dependencies,
            },
        )
        keyframe_media: list[CanvasMedia] = []
        if keyframe_artifact is not None:
            item = stager.add(keyframe_artifact.path, kind="image", sha256=keyframe_artifact.sha256)
            keyframe_media.append(item.model_copy(update={"role": "final_canvas"}))
        add_node(
            node_id=keyframe_id,
            kind="image",
            title=f"Shot {shot.order} Canvas",
            summary="Final keyframe with ordered visual dependencies"
            if keyframe_media
            else "Keyframe not generated in this run",
            stage=5,
            x=x,
            y=430,
            width=420,
            height=330,
            media=keyframe_media,
            details={
                "planned_prompt": planned,
                "actual_prompt": actual,
                "input_mode": keyframe_artifact.input_mode.value
                if keyframe_artifact and keyframe_artifact.input_mode
                else None,
                "ordered_references": reference_details_by_shot[shot.shot_id],
                "receipt": _receipt_details(keyframe_artifact),
            },
        )
        add_node(
            node_id=video_prompt_id,
            kind="prompt",
            title=f"Shot {shot.order} Video Prompt",
            summary=_redact_text(shot.h3_prompt, roots)[:180],
            stage=5,
            x=x,
            y=830,
            width=420,
            height=240,
            details={
                "h3_prompt": _redact_text(shot.h3_prompt, roots),
                "duration_seconds": shot.duration_seconds,
            },
        )
        video_media: list[CanvasMedia] = []
        if video_artifact is not None:
            item = stager.add(video_artifact.path, kind="video", sha256=video_artifact.sha256)
            video_media.append(item.model_copy(update={"role": "shot_video"}))
            receipt_rows.append(_receipt_details(video_artifact))
        add_node(
            node_id=video_id,
            kind="video",
            title=f"Shot {shot.order} Video",
            summary="Audited video artifact" if video_media else "Video not generated in this run",
            stage=6,
            x=x,
            y=1140,
            width=420,
            height=330,
            media=video_media,
            details={"receipt": _receipt_details(video_artifact)},
        )
        video_nodes.append(video_id)
        add_edge("media-dag", shot_prompt_id, "instantiate", 4)
        add_edge(shot_prompt_id, keyframe_id, "image prompt", 5)
        add_edge(keyframe_id, video_prompt_id, "Canvas first", 5)
        add_edge(video_prompt_id, video_id, "generate", 6)
        if shot.previous_shot_id and shot.previous_shot_id in keyframe_node_by_shot:
            add_edge(
                keyframe_node_by_shot[shot.previous_shot_id],
                keyframe_id,
                "previous shot",
                5,
                dashed=True,
            )

    output_x = shot_start_x + len(plan.shots) * shot_gap + 100
    add_node(
        node_id="run-receipts",
        kind="receipt",
        title="Receipts & SHA",
        summary=f"{len(manifest.artifacts)} artifacts · {len(manifest.errors)} errors",
        stage=6,
        x=output_x,
        y=550,
        width=360,
        height=250,
        details={
            "call_counts": manifest.call_counts,
            "estimated_cost": manifest.estimated_cost,
            "artifacts": receipt_rows,
            "errors": _redact_value(manifest.errors, roots),
        },
    )
    story_artifact = artifacts.get("story-video")
    story_media: list[CanvasMedia] = []
    if story_artifact is not None:
        item = stager.add(story_artifact.path, kind="video", sha256=story_artifact.sha256)
        story_media.append(item.model_copy(update={"role": "story_video"}))
    add_node(
        node_id="story-output",
        kind="output",
        title="Inspectable Story Run",
        summary="Assembled story video"
        if story_media
        else f"{manifest.status.value} · {manifest.policy.mode.value} · no assembled video",
        stage=6,
        x=output_x,
        y=980,
        width=420,
        height=360,
        media=story_media,
        details={"receipt": _receipt_details(story_artifact)},
    )
    add_edge("media-dag", "run-receipts", "audit", 6, dashed=True)
    for video_node in video_nodes:
        add_edge(video_node, "story-output", "assemble", 6)
    add_edge("run-receipts", "story-output", "verify", 6)

    graph = CanvasGraph(
        run_id=manifest.run_id,
        plan_id=plan.plan_id,
        title=plan.title,
        subtitle="A persistent, inspectable media dependency graph",
        stages=STAGES,
        board={"width": output_x + 520, "height": 1580},
        nodes=nodes,
        edges=edges,
    )
    graph = CanvasGraph.model_validate(_redact_value(graph.model_dump(mode="json"), roots))
    graph.graph_sha256 = sha256_json(_graph_without_sha(graph))
    return graph, sorted(stager.records.values(), key=lambda item: item["path"])


def export_story_canvas(run_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    run_root = Path(run_dir).expanduser().resolve(strict=True)
    if not run_root.is_dir():
        raise ValueError(f"Run directory is not a directory: {run_root}")
    plan_path = _inside(run_root / "canvas_plan.json", run_root, label="Canvas plan")
    manifest_path = _inside(run_root / "run_manifest.json", run_root, label="Manifest")
    plan = CanvasPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    graph, media = build_canvas_graph(
        plan,
        manifest,
        run_root=run_root,
        output_root=output_root,
    )
    graph_path = output_root / "canvas_graph.json"
    atomic_write_json(graph_path, graph)
    template_path = Path(__file__).with_name("templates") / "canvas_viewer.html"
    template = Environment(autoescape=True).from_string(template_path.read_text(encoding="utf-8"))
    html = template.render(
        title=graph.title,
        graph_json=graph.model_dump(mode="json", exclude_none=True),
    )
    index_path = output_root / "index.html"
    atomic_write_text(index_path, html)
    report = {
        "schema_version": "storycanvas/canvas-export/v1",
        "run_id": manifest.run_id,
        "plan_id": plan.plan_id,
        "graph_sha256": graph.graph_sha256,
        "inputs": {
            "canvas_plan_sha256": sha256_file(plan_path),
            "run_manifest_sha256": sha256_file(manifest_path),
        },
        "outputs": {
            "index": "index.html",
            "graph": "canvas_graph.json",
            "index_sha256": sha256_file(index_path),
            "graph_file_sha256": sha256_file(graph_path),
        },
        "counts": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "media": len(media),
        },
        "media": media,
        "network_calls": False,
        "comfyui_required": False,
    }
    atomic_write_json(output_root / "export_report.json", report)
    return report
