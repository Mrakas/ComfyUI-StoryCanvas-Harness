from __future__ import annotations

import errno
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .media import full_decode, probe_media
from .schemas import ArtifactRecord, CanvasPlan, RunManifest
from .utils import atomic_write_json, canonical_json, ensure_safe_id, sha256_file, sha256_json

NODE_TEXT_PREVIEW = "StoryCanvasTextPreview"
NODE_IMAGE_PREVIEW = "StoryCanvasImagePreview"
NODE_VIDEO_PREVIEW = "StoryCanvasVideoPreview"

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|secret)\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class StagedMedia:
    source: Path
    target: Path
    input_relative_path: str
    sha256: str
    kind: str
    transfer_mode: str


def _inside(path: Path, root: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {path}")
    resolved = expanded.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the Run directory: {path}") from exc
    return resolved


def _declared_source(declared: str, run_root: Path, recorded_root: Path | None) -> Path:
    value = Path(declared).expanduser()
    if value.is_absolute():
        if recorded_root is None:
            raise ValueError(f"Absolute media path has no recorded Run root: {declared}")
        try:
            relative = value.relative_to(recorded_root)
        except ValueError as exc:
            raise ValueError(
                f"Declared media path is outside the recorded Run root: {declared}"
            ) from exc
        candidate = run_root / relative
    else:
        candidate = run_root / value
    return _inside(candidate, run_root, label="Declared media")


def _validate_image(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return {"width": image.width, "height": image.height, "format": image.format}


def _validate_video(path: Path) -> dict[str, Any]:
    probe = probe_media(path)
    streams = probe.get("streams") or []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise ValueError(f"Video has no video stream: {path}")
    full_decode(path)
    return {
        "duration": (probe.get("format") or {}).get("duration"),
        "streams": [stream.get("codec_type") for stream in streams],
    }


def _safe_relative(path: str) -> str:
    value = Path(path)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"Unsafe ComfyUI input-relative path: {path}")
    normalized = value.as_posix().lstrip("/")
    if not normalized:
        raise ValueError("Empty ComfyUI input-relative path")
    return normalized


def _stage_file(source: Path, target: Path, digest: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or sha256_file(target) != digest:
            raise ValueError(f"Existing staged media has a different SHA256: {target}")
        same_inode = (
            source.stat().st_dev == target.stat().st_dev
            and source.stat().st_ino == target.stat().st_ino
        )
        return "existing_hardlink" if same_inode else "existing_copy"

    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        try:
            os.link(source, temporary)
            mode = "hardlink"
        except OSError as exc:
            if exc.errno not in {
                errno.EXDEV,
                errno.EPERM,
                errno.EACCES,
                errno.ENOTSUP,
                errno.EMLINK,
            }:
                raise
            shutil.copy2(source, temporary)
            mode = "copy"
        if sha256_file(temporary) != digest:
            raise ValueError(f"Staged media SHA256 mismatch: {source}")
        os.replace(temporary, target)
        return mode
    finally:
        if temporary.exists():
            temporary.unlink()


def _redact(value: str, run_root: Path, recorded_root: Path | None) -> str:
    redacted = value.replace(str(run_root), "[RUN_ROOT]")
    if recorded_root is not None:
        redacted = redacted.replace(str(recorded_root), "[RUN_ROOT]")
    return _SENSITIVE_PATTERN.sub("[REDACTED]", redacted)


def _load_prompt_records(run_root: Path) -> dict[str, dict[str, Any]]:
    path = run_root / "prompts.jsonl"
    if not path.is_file():
        raise ValueError(f"Missing Prompt records: {path}")
    records: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        asset_id = row.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError(f"Prompt record {line_number} has no asset_id")
        if asset_id in records:
            raise ValueError(f"Duplicate Prompt record: {asset_id}")
        records[asset_id] = row
    return records


def _artifact_map(manifest: RunManifest) -> dict[str, ArtifactRecord]:
    result: dict[str, ArtifactRecord] = {}
    for artifact in manifest.artifacts:
        if artifact.artifact_id in result:
            raise ValueError(f"Duplicate artifact_id: {artifact.artifact_id}")
        result[artifact.artifact_id] = artifact
    return result


def _receipt_summary(artifact: ArtifactRecord) -> str:
    if artifact.receipt is None:
        return "Receipt: local assembled artifact"
    return (
        f"Receipt: {artifact.receipt.provider} / {artifact.receipt.model} / "
        f"{artifact.receipt.operation}\nRequest SHA256: {artifact.receipt.request_sha256}"
    )


def _display_prompt(
    artifact: ArtifactRecord,
    prompt_record: dict[str, Any] | None,
    run_root: Path,
    recorded_root: Path | None,
) -> tuple[str, str]:
    planned = (prompt_record or {}).get("planned_prompt") or artifact.prompt or "N/A"
    actual = (
        artifact.metadata.get("actual_prompt")
        or artifact.prompt
        or (prompt_record or {}).get("actual_prompt")
        or "N/A"
    )
    return _redact(str(planned), run_root, recorded_root), _redact(
        str(actual), run_root, recorded_root
    )


class _ReviewGraph:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.links: list[list[Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.outputs: list[int] = []
        self.app_inputs: list[int] = []
        self._next_node = 1
        self._next_link = 1

    def add(
        self,
        node_type: str,
        title: str,
        position: tuple[float, float],
        widgets: list[Any],
        *,
        size: tuple[float, float],
        previous: int | None = None,
    ) -> int:
        node_id = self._next_node
        self._next_node += 1
        dependency_link = None
        if previous is not None:
            dependency_link = self._next_link
            self._next_link += 1
            self.links.append([dependency_link, previous, 0, node_id, len(widgets), "SC_REVIEW"])
            origin = next(item for item in self.nodes if item["id"] == previous)
            origin["outputs"][0]["links"].append(dependency_link)
        inputs: list[dict[str, Any]] = [
            {"name": name, "type": "STRING", "widget": {"name": name}, "link": None}
            for name in ("title", "text" if node_type == NODE_TEXT_PREVIEW else "files_json")
        ]
        inputs.append(
            {
                "name": "dependency",
                "type": "SC_REVIEW",
                "shape": 7,
                "link": dependency_link,
            }
        )
        node = {
            "id": node_id,
            "type": node_type,
            "pos": list(position),
            "size": list(size),
            "flags": {},
            "order": len(self.nodes),
            "mode": 0,
            "inputs": inputs,
            "outputs": [{"name": "review", "type": "SC_REVIEW", "links": []}],
            "title": title,
            "properties": {
                "Node name for S&R": node_type,
                "storycanvas_review_only": True,
            },
            "widgets_values": widgets,
        }
        self.nodes.append(node)
        self.outputs.append(node_id)
        if node_type == NODE_TEXT_PREVIEW:
            self.app_inputs.append(node_id)
        return node_id

    def group(self, title: str, bounding: list[float], color: str) -> None:
        self.groups.append(
            {
                "title": title,
                "bounding": bounding,
                "color": color,
                "font_size": 24,
                "flags": {},
            }
        )

    def workflow(self, metadata: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"storycanvas-review:{metadata['run_id']}")
        )
        return {
            "id": workflow_id,
            "revision": 0,
            "last_node_id": self._next_node - 1,
            "last_link_id": self._next_link - 1,
            "nodes": self.nodes,
            "links": self.links,
            "groups": self.groups,
            "definitions": {"subgraphs": []},
            "config": {},
            "extra": {
                "workflowRendererVersion": "LG",
                # Open on the media dependency graph. App Mode stays available
                # from ComfyUI's mode switcher for gallery-style review.
                "linearMode": False,
                "linearData": {
                    "inputs": [
                        [f"{workflow_id}:{node_id}:text", "text"] for node_id in self.app_inputs
                    ],
                    "outputs": [str(node_id) for node_id in self.outputs],
                },
                "storycanvas": metadata,
                "storycanvas_review_layout": "media_dag",
            },
            "version": 0.4,
        }


def _files_json(paths: list[str]) -> str:
    return json.dumps(paths, ensure_ascii=False, separators=(",", ":"))


def _story_text(plan: CanvasPlan, manifest: RunManifest) -> str:
    shot_lines = "\n".join(
        f"{shot.order}. {shot.title or shot.shot_id}: {shot.original_prompt}" for shot in plan.shots
    )
    continuity = "\n".join(f"- {rule}" for rule in plan.visual_bible.continuity_rules)
    return (
        f"Story: {plan.title}\nStory ID: {plan.story_id}\nRun ID: {manifest.run_id}\n"
        f"Status: {manifest.status.value}\n\nShots\n{shot_lines}\n\nContinuity rules\n{continuity}"
    )


def _app_filename(plan: CanvasPlan) -> str:
    parts = [part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", plan.story_id) if part]
    return f"StoryCanvas-{'-'.join(parts) or 'Review'}.app.json"


def import_comfy_review(run_dir: Path, comfy_root: Path) -> dict[str, Any]:
    run_root = run_dir.expanduser().resolve(strict=True)
    comfy = comfy_root.expanduser().resolve(strict=True)
    if not run_root.is_dir():
        raise ValueError(f"Run directory is not a directory: {run_root}")
    if not (comfy / "input").is_dir() or not (comfy / "user").is_dir():
        raise ValueError(f"Not a ComfyUI root: {comfy}")

    manifest_path = _inside(run_root / "run_manifest.json", run_root, label="Manifest")
    plan_path = _inside(run_root / "canvas_plan.json", run_root, label="Canvas plan")
    prompts_path = _inside(run_root / "prompts.jsonl", run_root, label="Prompt records")
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    plan = CanvasPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    ensure_safe_id(manifest.run_id, label="run_id")
    if manifest.plan_id != plan.plan_id:
        raise ValueError("Run manifest plan_id does not match canvas_plan.json")
    prompt_records = _load_prompt_records(run_root)
    artifacts = _artifact_map(manifest)
    recorded_root = Path(manifest.run_root).expanduser() if manifest.run_root else None

    stage_root = comfy / "input" / "storycanvas" / manifest.run_id
    staged_by_source: dict[Path, StagedMedia] = {}
    validations: list[dict[str, Any]] = []

    def stage(source: Path, kind: str, expected_sha: str | None = None) -> StagedMedia:
        resolved = _inside(source, run_root, label="Media")
        digest = sha256_file(resolved)
        if expected_sha is not None and digest != expected_sha:
            raise ValueError(
                f"SHA256 mismatch for {resolved}: expected {expected_sha}, got {digest}"
            )
        if resolved in staged_by_source:
            return staged_by_source[resolved]
        relative = resolved.relative_to(run_root)
        target = stage_root / relative
        if kind == "image":
            media_validation = _validate_image(resolved)
        elif kind == "video":
            media_validation = _validate_video(resolved)
        else:
            raise ValueError(f"Unsupported review media kind: {kind}")
        transfer_mode = _stage_file(resolved, target, digest)
        input_relative = _safe_relative(
            (Path("storycanvas") / manifest.run_id / relative).as_posix()
        )
        item = StagedMedia(resolved, target, input_relative, digest, kind, transfer_mode)
        staged_by_source[resolved] = item
        validations.append({"path": relative.as_posix(), "kind": kind, **media_validation})
        return item

    staged_artifacts: dict[str, StagedMedia] = {}
    for artifact in manifest.artifacts:
        if artifact.kind not in {"image", "video"}:
            continue
        source = _declared_source(artifact.path, run_root, recorded_root)
        staged_artifacts[artifact.artifact_id] = stage(source, artifact.kind, artifact.sha256)
        for reference in artifact.ordered_references:
            if reference.path and reference.sha256:
                stage(
                    _declared_source(reference.path, run_root, recorded_root),
                    "image",
                    reference.sha256,
                )

    midframes: dict[str, StagedMedia] = {}
    for shot in plan.shots:
        midframe = run_root / "midframes" / f"{shot.shot_id}.png"
        if not midframe.is_file():
            raise ValueError(f"Missing fixed 5-second midframe: {midframe}")
        midframes[shot.shot_id] = stage(midframe, "image")

    style_artifact = artifacts.get("style-bible")
    if style_artifact is None or "style-bible" not in staged_artifacts:
        raise ValueError("Run has no staged style-bible image artifact")
    style_planned, style_actual = _display_prompt(
        style_artifact,
        prompt_records.get(style_artifact.artifact_id),
        run_root,
        recorded_root,
    )

    graph = _ReviewGraph()
    previous: int | None = None
    previous = graph.add(
        NODE_TEXT_PREVIEW,
        "Story · Read-only Run",
        (80, 100),
        ["Story", _story_text(plan, manifest)],
        size=(520, 360),
        previous=previous,
    )
    graph.group("Story", [40, 50, 600, 460], "#2B5D6E")
    previous = graph.add(
        NODE_TEXT_PREVIEW,
        "Visual Bible · Prompt & Receipt",
        (720, 90),
        [
            "Visual Bible",
            f"Planned ImageGen Prompt\n{style_planned}\n\nActual successful Prompt\n{style_actual}\n\n{_receipt_summary(style_artifact)}",
        ],
        size=(540, 420),
        previous=previous,
    )
    previous = graph.add(
        NODE_IMAGE_PREVIEW,
        "Visual Bible · Generated Image",
        (720, 560),
        ["Visual Bible image", _files_json([staged_artifacts["style-bible"].input_relative_path])],
        size=(540, 420),
        previous=previous,
    )
    graph.group("Visual Bible", [680, 40, 620, 980], "#467A65")

    shot_x = 1380
    for shot in plan.shots:
        shot_artifact = artifacts.get(shot.keyframe_asset_id)
        video_artifact = artifacts.get(f"{shot.shot_id}-video")
        if shot_artifact is None or video_artifact is None:
            raise ValueError(f"Shot {shot.shot_id} is missing its keyframe or video artifact")
        if (
            shot_artifact.artifact_id not in staged_artifacts
            or video_artifact.artifact_id not in staged_artifacts
        ):
            raise ValueError(f"Shot {shot.shot_id} media was not staged")
        planned, actual = _display_prompt(
            shot_artifact,
            prompt_records.get(shot_artifact.artifact_id),
            run_root,
            recorded_root,
        )
        references = [
            staged_by_source[
                _declared_source(reference.path, run_root, recorded_root)
            ].input_relative_path
            for reference in shot_artifact.ordered_references
            if reference.path
        ]
        reference_lines = (
            "\n".join(
                f"{reference.order}. {reference.role} [{reference.provenance.value}] · {reference.sha256}"
                for reference in shot_artifact.ordered_references
            )
            or "No image references"
        )
        text = (
            f"Original Shot Prompt\n{shot.original_prompt}\n\n"
            f"Planned ImageGen Prompt\n{planned}\n\nActual successful ImageGen Prompt\n{actual}\n\n"
            f"Ordered references\n{reference_lines}\n\nH3 Prompt\n{shot.h3_prompt}\n\n"
            f"Previous Shot dependency: {shot.previous_shot_id or 'None'}\n"
            f"{_receipt_summary(shot_artifact)}\nVideo SHA256: {video_artifact.sha256}"
        )
        previous = graph.add(
            NODE_TEXT_PREVIEW,
            f"Shot {shot.order} · {shot.title or shot.shot_id} · Prompts",
            (shot_x, 90),
            [f"Shot {shot.order} · Prompts", text],
            size=(600, 560),
            previous=previous,
        )
        if references:
            previous = graph.add(
                NODE_IMAGE_PREVIEW,
                f"Shot {shot.order} · Ordered References",
                (shot_x, 700),
                [f"Shot {shot.order} · Ordered references", _files_json(references)],
                size=(600, 380),
                previous=previous,
            )
        previous = graph.add(
            NODE_IMAGE_PREVIEW,
            f"Shot {shot.order} · Final Canvas",
            (shot_x, 1130),
            [
                f"Shot {shot.order} · Final Canvas",
                _files_json([staged_artifacts[shot_artifact.artifact_id].input_relative_path]),
            ],
            size=(600, 420),
            previous=previous,
        )
        previous = graph.add(
            NODE_IMAGE_PREVIEW,
            f"Shot {shot.order} · H3 5s Midframe",
            (shot_x, 1600),
            [
                f"Shot {shot.order} · 5s midframe",
                _files_json([midframes[shot.shot_id].input_relative_path]),
            ],
            size=(600, 420),
            previous=previous,
        )
        previous = graph.add(
            NODE_VIDEO_PREVIEW,
            f"Shot {shot.order} · MiniMax-H3 Video",
            (shot_x, 2070),
            [
                f"Shot {shot.order} · Video",
                _files_json([staged_artifacts[video_artifact.artifact_id].input_relative_path]),
            ],
            size=(600, 420),
            previous=previous,
        )
        graph.group(
            f"Shot {shot.order} · {shot.title or shot.shot_id}",
            [shot_x - 40, 40, 680, 2500],
            "#6A4C93" if shot.order % 2 else "#805A46",
        )
        shot_x += 720

    story_video = staged_artifacts.get("story-video")
    if story_video is None:
        raise ValueError("Run has no story-video artifact")
    previous = graph.add(
        NODE_VIDEO_PREVIEW,
        "Story Video · All Shots",
        (shot_x, 160),
        ["The Moon Garden · Complete story video", _files_json([story_video.input_relative_path])],
        size=(680, 500),
        previous=previous,
    )
    graph.group("Story Video", [shot_x - 40, 90, 760, 650], "#AA7A2D")

    workflow = graph.workflow(
        {
            "schema_version": plan.schema_version,
            "review_only": True,
            "network_calls": False,
            "run_id": manifest.run_id,
            "plan_id": plan.plan_id,
            "story_id": plan.story_id,
            "shot_count": len(plan.shots),
            "workflow_sha256": "pending",
        }
    )
    workflow["extra"]["storycanvas"]["workflow_sha256"] = sha256_json(workflow)
    serialized = canonical_json(workflow)
    forbidden = [str(run_root), str(recorded_root) if recorded_root else "", "MINIMAX_H3_API_KEY"]
    if any(item and item in serialized for item in forbidden):
        raise ValueError("Review workflow contains an absolute source path or credential name")
    if any(
        node["type"] not in {NODE_TEXT_PREVIEW, NODE_IMAGE_PREVIEW, NODE_VIDEO_PREVIEW}
        for node in workflow["nodes"]
    ):
        raise ValueError("Review workflow contains an execution node")

    review_workflow = run_root / "review_workflow.json"
    app_path = comfy / "user" / "default" / "workflows" / _app_filename(plan)
    atomic_write_json(review_workflow, workflow)
    atomic_write_json(app_path, workflow)

    receipt_files = sorted((run_root / "receipts").glob("*.json"))
    report = {
        "schema_version": "storycanvas/review-import/v1",
        "run_id": manifest.run_id,
        "plan_id": plan.plan_id,
        "review_only": True,
        "network_calls": False,
        "source": {
            "run_dir": str(run_root),
            "run_manifest": str(manifest_path),
            "run_manifest_sha256": sha256_file(manifest_path),
            "canvas_plan_sha256": sha256_file(plan_path),
            "prompts_sha256": sha256_file(prompts_path),
            "receipt_sha256": [
                {"path": path.relative_to(run_root).as_posix(), "sha256": sha256_file(path)}
                for path in receipt_files
            ],
        },
        "target": {
            "comfy_root": str(comfy),
            "input_root": str(stage_root),
            "review_workflow": str(review_workflow),
            "app_workflow": str(app_path),
        },
        "media": [
            {
                "source": str(item.source),
                "target": str(item.target),
                "input_relative_path": item.input_relative_path,
                "sha256": item.sha256,
                "kind": item.kind,
                "transfer_mode": item.transfer_mode,
            }
            for item in staged_by_source.values()
        ],
        "validations": validations,
        "counts": {
            "generated_images": sum(
                artifact.kind == "image" and artifact.provenance is not None
                for artifact in manifest.artifacts
            ),
            "midframes": len(midframes),
            "shot_videos": len(plan.shots),
            "story_videos": 1,
            "staged_unique_media": len(staged_by_source),
            "app_outputs": len(workflow["extra"]["linearData"]["outputs"]),
        },
        "workflow_sha256": sha256_file(review_workflow),
    }
    atomic_write_json(run_root / "review_import_report.json", report)
    return report
