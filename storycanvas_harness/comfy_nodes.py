from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from .audit import render_audit
from .engine import StoryCanvas
from .errors import ProviderError
from .media import concat_videos, full_decode, probe_media
from .schemas import (
    ArtifactRecord,
    CanvasPlan,
    ExecutionMode,
    ExecutionPolicy,
    InputMode,
    PlannedReference,
    Provenance,
    RunManifest,
    RunStatus,
    ShotInput,
    StoryInput,
)
from .utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    sha256_file,
    sha256_json,
    utc_now,
)

CATEGORY = "StoryCanvas"
MAX_COMFY_SHOTS = 24
_ENGINE: StoryCanvas | None = None
_ENGINE_LOCK = Lock()


def _engine() -> StoryCanvas:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = StoryCanvas.from_environment()
    return _ENGINE


def _plan(value: Any) -> CanvasPlan:
    return value if isinstance(value, CanvasPlan) else CanvasPlan.model_validate(value)


def _policy(value: Any) -> ExecutionPolicy:
    return value if isinstance(value, ExecutionPolicy) else ExecutionPolicy.model_validate(value)


def _runtime_root(plan: CanvasPlan, policy: ExecutionPolicy) -> Path:
    run_id = f"comfy-{sha256_json({'plan_id': plan.plan_id, 'policy': policy})[:16]}"
    root = _engine().runs_dir / run_id
    for directory in (
        root / "assets",
        root / "videos" / "shots",
        root / "receipts",
        root / "prompt_records",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / "canvas_plan.json", plan)
    return root


def _write_prompt_record(root: Path, artifact_id: str, row: dict[str, Any]) -> None:
    atomic_write_json(root / "prompt_records" / f"{artifact_id}.json", row)


def _artifact_asset(artifact: ArtifactRecord) -> dict[str, Any]:
    return {
        "asset_id": artifact.artifact_id,
        "path": artifact.path,
        "sha256": artifact.sha256,
        "role": artifact.metadata.get("role") or artifact.metadata.get("asset_kind"),
        "provenance": artifact.provenance.value if artifact.provenance else None,
        "status": "ready",
        "artifact": artifact.model_dump(mode="json", exclude_none=True),
    }


def _pack_generated(reference_pack: dict[str, Any] | None) -> dict[str, ArtifactRecord]:
    generated: dict[str, ArtifactRecord] = {}
    for asset in (reference_pack or {}).get("assets", []):
        artifact = asset.get("artifact")
        if artifact:
            parsed = ArtifactRecord.model_validate(artifact)
            generated[parsed.artifact_id] = parsed
    return generated


class StoryCanvasInputNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "input_kind": (["shot", "story"], {"default": "shot"}),
                "payload_json": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": json.dumps(
                            {"prompt": "A fictional clockmaker repairs a tiny mechanical bird."},
                            indent=2,
                        ),
                    },
                ),
            }
        }

    RETURN_TYPES = ("SC_INPUT",)
    RETURN_NAMES = ("story_input",)
    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(self, input_kind: str, payload_json: str) -> tuple[dict[str, Any]]:
        payload = json.loads(payload_json)
        return ({"input_kind": input_kind, "payload": payload},)


class StoryCanvasExecutionPolicyNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "policy_json": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": canonical_json(ExecutionPolicy()),
                    },
                )
            }
        }

    RETURN_TYPES = ("SC_POLICY",)
    RETURN_NAMES = ("policy",)
    FUNCTION = "validate"
    CATEGORY = CATEGORY

    def validate(self, policy_json: str) -> tuple[dict[str, Any]]:
        policy = ExecutionPolicy.model_validate_json(policy_json)
        return (policy.model_dump(mode="json"),)


class StoryCanvasDirectorNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "story_input": ("SC_INPUT",),
                "policy": ("SC_POLICY",),
                "provider": (["precomputed", "openai", "offline_preview"],),
                "precomputed_plan_json": (
                    "STRING",
                    {"multiline": True, "default": ""},
                ),
            }
        }

    RETURN_TYPES = ("SC_PLAN",)
    RETURN_NAMES = ("plan",)
    FUNCTION = "direct"
    CATEGORY = CATEGORY

    def direct(
        self,
        story_input: dict[str, Any],
        policy: dict[str, Any],
        provider: str,
        precomputed_plan_json: str,
    ) -> tuple[dict[str, Any]]:
        selected_policy = _policy(policy)
        if provider == "precomputed":
            if not precomputed_plan_json.strip():
                raise ProviderError("precomputed_plan_json is empty")
            plan = CanvasPlan.model_validate_json(precomputed_plan_json)
        else:
            payload = story_input["payload"]
            value = (
                ShotInput.model_validate(payload)
                if story_input["input_kind"] == "shot"
                else StoryInput.model_validate(payload)
            )
            runtime = _engine()
            if provider == "offline_preview":
                runtime = StoryCanvas(runs_dir=runtime.runs_dir)
            plan = runtime.plan(value, selected_policy)
        return (plan.model_dump(mode="json", exclude_none=True),)


class StoryCanvasSharedVisualAssetNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "plan": ("SC_PLAN",),
                "policy": ("SC_POLICY",),
                "asset_id": ("STRING", {"default": "style-bible"}),
            }
        }

    RETURN_TYPES = ("SC_ASSET",)
    RETURN_NAMES = ("asset",)
    FUNCTION = "resolve"
    CATEGORY = f"{CATEGORY}/Assets"

    def resolve(
        self, plan: dict[str, Any], policy: dict[str, Any], asset_id: str
    ) -> tuple[dict[str, Any]]:
        parsed_plan = _plan(plan)
        parsed_policy = _policy(policy)
        asset = next(
            (item for item in parsed_plan.shared_assets if item.asset_id == asset_id), None
        )
        if asset is None:
            raise ProviderError(f"Unknown shared asset: {asset_id}")
        if parsed_policy.mode == ExecutionMode.PLAN_ONLY:
            return (
                {
                    "asset_id": asset_id,
                    "role": asset.role,
                    "status": "planned",
                    "prompt": asset.actual_prompt,
                    "provenance": Provenance.IMAGE_GENERATION.value,
                },
            )
        runtime = _engine()
        if runtime.image_provider is None:
            raise ProviderError("No image provider is configured")
        root = _runtime_root(parsed_plan, parsed_policy)
        actual_prompt = asset.actual_prompt
        references = list(asset.references)
        if asset.search_query:
            result, _ = runtime._cached_search(
                root=root,
                asset_id=asset_id,
                query=asset.search_query,
                provider=runtime.fact_search,
                kind="fact-search",
            )
            actual_prompt += f"\n\nVerified visual facts:\n{result.summary}"
        if asset.visual_search_query:
            result, _ = runtime._cached_search(
                root=root,
                asset_id=asset_id,
                query=asset.visual_search_query,
                provider=runtime.visual_search,
                kind="visual-search",
            )
            source = runtime._visual_source_artifact(
                root=root,
                asset_id=asset_id,
                query=asset.visual_search_query,
                result=result,
            )
            if len(references) >= 5:
                raise ProviderError(f"Asset {asset_id} has no free slot for a visual exemplar")
            references.append(
                PlannedReference(
                    order=len(references) + 1,
                    reference_id=source.artifact_id,
                    role="visual_search_exemplar",
                    provenance=Provenance.VISUAL_SEARCH,
                    path=source.path,
                    sha256=source.sha256,
                    description=f"Selected from {source.metadata.get('page_url')}",
                )
            )
        artifact, prompt_row = runtime._generate_image(
            root=root,
            artifact_id=asset.asset_id,
            prompt=actual_prompt,
            planned_prompt=asset.planned_prompt,
            references=references,
            generated={},
            metadata={"asset_kind": asset.kind, "role": asset.role},
        )
        _write_prompt_record(root, artifact.artifact_id, prompt_row)
        return (_artifact_asset(artifact),)


class StoryCanvasReferenceAssetNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "plan": ("SC_PLAN",),
                "policy": ("SC_POLICY",),
                "asset_id": ("STRING", {"default": ""}),
                "shot_id": ("STRING", {"default": "shot-001"}),
                "reference_id": ("STRING", {"default": ""}),
            },
            "optional": {"reference_pack": ("SC_REF_PACK",)},
        }

    RETURN_TYPES = ("SC_ASSET",)
    RETURN_NAMES = ("asset",)
    FUNCTION = "resolve"
    CATEGORY = f"{CATEGORY}/Assets"

    def resolve(
        self,
        plan: dict[str, Any],
        policy: dict[str, Any],
        asset_id: str,
        shot_id: str,
        reference_id: str,
        reference_pack: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any]]:
        parsed_plan = _plan(plan)
        parsed_policy = _policy(policy)
        shot = next((item for item in parsed_plan.shots if item.shot_id == shot_id), None)
        if shot is None:
            raise ProviderError(f"Unknown shot: {shot_id}")
        if not asset_id:
            reference = next(
                (item for item in shot.references if item.reference_id == reference_id), None
            )
            if reference is None or not reference.path:
                raise ProviderError(f"Reference is not a direct file: {reference_id}")
            path = Path(reference.path).expanduser()
            if not path.is_file():
                raise ProviderError(f"Reference image does not exist: {path}")
            digest = sha256_file(path)
            if reference.sha256 and reference.sha256 != digest:
                raise ProviderError(f"Reference SHA mismatch: {reference_id}")
            return (
                {
                    "asset_id": reference.reference_id,
                    "path": str(path),
                    "sha256": digest,
                    "role": reference.role,
                    "provenance": reference.provenance.value,
                    "status": "ready",
                },
            )
        if asset_id != shot.keyframe_asset_id:
            raise ProviderError(f"Generated reference asset does not match {shot.shot_id}")
        if parsed_policy.mode == ExecutionMode.PLAN_ONLY:
            return (
                {
                    "asset_id": asset_id,
                    "role": "final_canvas",
                    "status": "planned",
                    "prompt": shot.image_prompt,
                    "provenance": Provenance.GENERATED_CANVAS.value,
                },
            )
        runtime = _engine()
        if runtime.image_provider is None:
            raise ProviderError("No image provider is configured")
        root = _runtime_root(parsed_plan, parsed_policy)
        generated = _pack_generated(reference_pack)
        references: list[PlannedReference] = []
        for index, asset in enumerate((reference_pack or {}).get("assets", []), start=1):
            if asset.get("status") != "ready":
                raise ProviderError(f"Reference is not ready: {asset.get('asset_id')}")
            references.append(
                PlannedReference(
                    order=index,
                    reference_id=str(asset["asset_id"]),
                    role=str(asset.get("role") or "visual_reference"),
                    provenance=Provenance(str(asset.get("provenance") or "image_generation")),
                    source_asset_id=(str(asset["asset_id"]) if asset.get("artifact") else None),
                    path=str(asset["path"]) if not asset.get("artifact") else None,
                    sha256=str(asset["sha256"]),
                )
            )
        artifact, prompt_row = runtime._generate_image(
            root=root,
            artifact_id=asset_id,
            prompt=shot.image_prompt,
            planned_prompt=shot.image_prompt,
            references=references,
            generated=generated,
            metadata={"asset_kind": "shot_keyframe", "shot_id": shot_id, "role": "final_canvas"},
        )
        _write_prompt_record(root, artifact.artifact_id, prompt_row)
        return (_artifact_asset(artifact),)


class StoryCanvasReferencePackNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {"reference_count": ("INT", {"default": 1, "min": 0, "max": 9})},
            "optional": {f"reference_{index}": ("SC_ASSET",) for index in range(1, 10)},
        }

    RETURN_TYPES = ("SC_REF_PACK",)
    RETURN_NAMES = ("reference_pack",)
    FUNCTION = "pack"
    CATEGORY = f"{CATEGORY}/Assets"

    def pack(self, reference_count: int, **kwargs: Any) -> tuple[dict[str, Any]]:
        assets = []
        for index in range(1, reference_count + 1):
            item = kwargs.get(f"reference_{index}")
            if item is None:
                raise ProviderError(f"Reference {index} is declared but not connected")
            assets.append(item)
        return ({"assets": assets, "reference_count": len(assets)},)


class StoryCanvasH3PromptCompilerNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "plan": ("SC_PLAN",),
                "reference_pack": ("SC_REF_PACK",),
                "shot_id": ("STRING", {"default": "shot-001"}),
            }
        }

    RETURN_TYPES = ("SC_H3_PROMPT",)
    RETURN_NAMES = ("h3_prompt",)
    FUNCTION = "compile"
    CATEGORY = f"{CATEGORY}/Video"

    def compile(
        self, plan: dict[str, Any], reference_pack: dict[str, Any], shot_id: str
    ) -> tuple[dict[str, Any]]:
        parsed = _plan(plan)
        shot = next((item for item in parsed.shots if item.shot_id == shot_id), None)
        if shot is None:
            raise ProviderError(f"Unknown shot: {shot_id}")
        bindings = ["Ordered reference binding (do not reorder):"]
        for index, asset in enumerate(reference_pack.get("assets", []), start=1):
            bindings.append(
                f"Image {index}: {asset.get('role') or asset.get('asset_id')}; "
                f"source={asset.get('provenance') or 'unknown'}."
            )
        binding_text = "\n".join(bindings)
        prompt = f"{binding_text}\n\n{shot.h3_prompt}"
        return (
            {
                "shot_id": shot_id,
                "prompt": prompt,
                "prompt_sha256": sha256_json({"prompt": prompt}),
            },
        )


class StoryCanvasMiniMaxH3APINode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "plan": ("SC_PLAN",),
                "h3_prompt": ("SC_H3_PROMPT",),
                "reference_pack": ("SC_REF_PACK",),
                "policy": ("SC_POLICY",),
                "shot_id": ("STRING", {"default": "shot-001"}),
            }
        }

    RETURN_TYPES = ("SC_VIDEO",)
    RETURN_NAMES = ("video",)
    FUNCTION = "generate"
    CATEGORY = f"{CATEGORY}/Video"

    def generate(
        self,
        plan: dict[str, Any],
        h3_prompt: dict[str, Any],
        reference_pack: dict[str, Any],
        policy: dict[str, Any],
        shot_id: str,
    ) -> tuple[dict[str, Any]]:
        parsed_plan = _plan(plan)
        parsed_policy = _policy(policy)
        if parsed_policy.mode != ExecutionMode.FULL or not parsed_policy.allow_paid_video:
            return (
                {
                    "shot_id": shot_id,
                    "status": "locked",
                    "reason": "Paid video requires mode=full and allow_paid_video=true",
                },
            )
        runtime = _engine()
        if runtime.video_provider is None:
            raise ProviderError("No MiniMax-H3-compatible provider is configured")
        assets = reference_pack.get("assets", [])
        if not 1 <= len(assets) <= 9:
            raise ProviderError("MiniMax-H3 requires 1-9 references")
        paths = []
        for asset in assets:
            if asset.get("status") != "ready" or not asset.get("path"):
                raise ProviderError(f"H3 reference is not ready: {asset.get('asset_id')}")
            paths.append(Path(asset["path"]))
        root = _runtime_root(parsed_plan, parsed_policy)
        destination = root / "videos" / "shots" / f"{shot_id}.mp4"
        state_path = root / "receipts" / f"{shot_id}.video-task.json"
        generated = runtime.video_provider.generate(
            h3_prompt["prompt"], paths, destination, state_path
        )
        media = probe_media(destination)
        full_decode(destination)
        ordered_references = [
            PlannedReference(
                order=index,
                reference_id=str(asset["asset_id"]),
                role=str(asset.get("role") or "visual_reference"),
                provenance=Provenance(str(asset.get("provenance") or "image_generation")),
                path=str(asset["path"]),
                sha256=str(asset["sha256"]),
            )
            for index, asset in enumerate(assets, start=1)
        ]
        artifact = ArtifactRecord(
            artifact_id=f"{shot_id}-video",
            kind="video",
            path=str(destination),
            sha256=sha256_file(destination),
            prompt=str(h3_prompt["prompt"]),
            prompt_sha256=str(h3_prompt["prompt_sha256"]),
            input_mode=InputMode.TEXT_IMAGE,
            ordered_references=ordered_references,
            receipt=generated.receipt,
            metadata={**generated.metadata, "media": media, "shot_id": shot_id},
        )
        result = {
            "shot_id": shot_id,
            "status": "ready",
            "path": artifact.path,
            "sha256": artifact.sha256,
            "task_id": generated.receipt.task_id,
            "receipt": generated.receipt.model_dump(mode="json", exclude_none=True),
            "media": media,
        }
        atomic_write_json(
            root / "receipts" / f"{shot_id}.video-artifact.json", {"artifact": artifact}
        )
        _write_prompt_record(
            root,
            artifact.artifact_id,
            {
                "asset_id": artifact.artifact_id,
                "planned_prompt": artifact.prompt,
                "actual_prompt": artifact.prompt,
                "prompt_sha256": artifact.prompt_sha256,
                "input_mode": InputMode.TEXT_IMAGE.value,
                "ordered_references": [item.model_dump(mode="json") for item in ordered_references],
                "receipt": generated.receipt.model_dump(mode="json", exclude_none=True),
                "output_sha256": artifact.sha256,
            },
        )
        return (result,)


class StoryCanvasStoryAssembleNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "plan": ("SC_PLAN",),
                "policy": ("SC_POLICY",),
                "video_count": ("INT", {"default": 1, "min": 1, "max": MAX_COMFY_SHOTS}),
            },
            "optional": {
                f"video_{index}": ("SC_VIDEO",) for index in range(1, MAX_COMFY_SHOTS + 1)
            },
        }

    RETURN_TYPES = ("SC_VIDEO",)
    RETURN_NAMES = ("story_video",)
    FUNCTION = "assemble"
    CATEGORY = f"{CATEGORY}/Video"

    def assemble(
        self,
        plan: dict[str, Any],
        policy: dict[str, Any],
        video_count: int,
        **kwargs: Any,
    ) -> tuple[dict[str, Any]]:
        parsed_plan = _plan(plan)
        parsed_policy = _policy(policy)
        videos = [kwargs.get(f"video_{index}") for index in range(1, video_count + 1)]
        if parsed_policy.mode != ExecutionMode.FULL or not parsed_policy.allow_paid_video:
            return ({"status": "locked", "shot_count": video_count},)
        ready_videos: list[dict[str, Any]] = []
        for item in videos:
            if not isinstance(item, dict) or item.get("status") != "ready":
                return ({"status": "partial", "shot_count": video_count, "videos": videos},)
            ready_videos.append(item)
        if len(ready_videos) != video_count:
            return ({"status": "partial", "shot_count": video_count, "videos": videos},)
        root = _runtime_root(parsed_plan, parsed_policy)
        destination = root / "videos" / "story.mp4"
        concat_videos([Path(str(item["path"])) for item in ready_videos], destination)
        full_decode(destination)
        return (
            {
                "status": "ready",
                "path": str(destination),
                "sha256": sha256_file(destination),
                "shot_count": video_count,
            },
        )


class StoryCanvasRunManifestNode:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {"plan": ("SC_PLAN",), "policy": ("SC_POLICY",)},
            "optional": {"story_video": ("SC_VIDEO",)},
        }

    RETURN_TYPES = ("SC_MANIFEST",)
    RETURN_NAMES = ("manifest",)
    FUNCTION = "write"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def write(
        self,
        plan: dict[str, Any],
        policy: dict[str, Any],
        story_video: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any]]:
        parsed_plan = _plan(plan)
        parsed_policy = _policy(policy)
        root = _runtime_root(parsed_plan, parsed_policy)
        artifacts_by_id: dict[str, ArtifactRecord] = {}
        for receipt in sorted((root / "receipts").glob("*.json")):
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if payload.get("artifact"):
                artifact = ArtifactRecord.model_validate(payload["artifact"])
                artifacts_by_id[artifact.artifact_id] = artifact
        if story_video and story_video.get("status") == "ready":
            artifacts_by_id["story-video"] = ArtifactRecord(
                artifact_id="story-video",
                kind="video",
                path=story_video["path"],
                sha256=story_video["sha256"],
                metadata={"shot_count": story_video.get("shot_count")},
            )
        artifacts = list(artifacts_by_id.values())
        prompt_records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((root / "prompt_records").glob("*.json"))
        ]
        atomic_write_text(
            root / "prompts.jsonl",
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in prompt_records
            ),
        )
        expected_images = len(parsed_plan.shared_assets) + len(parsed_plan.shots)
        image_count = sum(
            item.kind == "image" and item.provenance == Provenance.IMAGE_GENERATION
            for item in artifacts
        )
        shot_video_count = sum(
            item.kind == "video"
            and item.artifact_id != "story-video"
            and item.artifact_id.endswith("-video")
            for item in artifacts
        )
        assets_complete = image_count == expected_images
        videos_complete = parsed_policy.mode != ExecutionMode.FULL or (
            shot_video_count == len(parsed_plan.shots)
            and story_video is not None
            and story_video.get("status") == "ready"
        )
        status = (
            RunStatus.COMPLETE
            if parsed_policy.mode == ExecutionMode.PLAN_ONLY
            or (assets_complete and videos_complete)
            else RunStatus.PARTIAL
        )
        manifest = RunManifest(
            run_id=root.name,
            plan_id=parsed_plan.plan_id,
            status=status,
            input_sha256=parsed_plan.input_sha256,
            policy=parsed_policy,
            run_root=str(root),
            artifacts=artifacts,
            call_counts={
                "image_generation": image_count,
                "visual_search_download": sum(
                    item.provenance == Provenance.VISUAL_SEARCH for item in artifacts
                ),
                "video_generation": shot_video_count,
            },
            finished_at=utc_now(),
        )
        atomic_write_json(root / "run_manifest.json", manifest)
        render_audit(parsed_plan, manifest, root / "audit.html")
        return (
            {
                "status": manifest.status.value,
                "path": str(root / "run_manifest.json"),
                "run_id": root.name,
            },
        )


NODE_CLASS_MAPPINGS = {
    "StoryCanvasInput": StoryCanvasInputNode,
    "StoryCanvasExecutionPolicy": StoryCanvasExecutionPolicyNode,
    "StoryCanvasDirector": StoryCanvasDirectorNode,
    "StoryCanvasSharedVisualAsset": StoryCanvasSharedVisualAssetNode,
    "StoryCanvasReferenceAsset": StoryCanvasReferenceAssetNode,
    "StoryCanvasReferencePack": StoryCanvasReferencePackNode,
    "StoryCanvasH3PromptCompiler": StoryCanvasH3PromptCompilerNode,
    "StoryCanvasMiniMaxH3API": StoryCanvasMiniMaxH3APINode,
    "StoryCanvasStoryAssemble": StoryCanvasStoryAssembleNode,
    "StoryCanvasRunManifest": StoryCanvasRunManifestNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StoryCanvasInput": "StoryCanvas Input",
    "StoryCanvasExecutionPolicy": "Execution Policy",
    "StoryCanvasDirector": "StoryCanvas Director",
    "StoryCanvasSharedVisualAsset": "Shared Visual Asset",
    "StoryCanvasReferenceAsset": "Reference Asset",
    "StoryCanvasReferencePack": "Reference Pack",
    "StoryCanvasH3PromptCompiler": "H3 Prompt Compiler",
    "StoryCanvasMiniMaxH3API": "MiniMax H3 API",
    "StoryCanvasStoryAssemble": "Story Assemble",
    "StoryCanvasRunManifest": "Run Manifest",
}


try:  # Register server routes only inside a real ComfyUI process.
    from .comfy_api import register_comfy_routes

    register_comfy_routes()
except (ImportError, ModuleNotFoundError):  # pragma: no cover - expected in SDK/test contexts
    pass
