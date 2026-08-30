from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from .utils import sha256_json, utc_now

SCHEMA_VERSION: Literal["storycanvas/v1"] = "storycanvas/v1"
SafeId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Filesystem-safe stable identifier",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ExecutionMode(str, Enum):
    PLAN_ONLY = "plan_only"
    ASSETS = "assets"
    FULL = "full"


class InputMode(str, Enum):
    TEXT = "text"
    TEXT_IMAGE = "text+image"


class Provenance(str, Enum):
    USER_REFERENCE = "user_reference"
    OFFICIAL_REFERENCE = "official_reference"
    FACT_SEARCH = "fact_search"
    VISUAL_SEARCH = "visual_search"
    IMAGE_GENERATION = "image_generation"
    PREVIOUS_SHOT = "previous_shot"
    GENERATED_CANVAS = "generated_canvas"


class RunStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class UserReference(StrictModel):
    reference_id: SafeId
    path: str
    role: str = "visual_reference"
    description: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ShotInput(StrictModel):
    prompt: str = Field(min_length=1)
    shot_id: SafeId | None = None
    title: str | None = None
    references: list[UserReference] = Field(default_factory=list, max_length=5)
    fixed_reference_plan: list[str] | None = Field(default=None, max_length=5)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoryInput(StrictModel):
    story_id: SafeId | None = None
    title: str | None = None
    free_text: str | None = None
    shots: list[ShotInput] = Field(default_factory=list)
    references: list[UserReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_story_content(self) -> StoryInput:
        if not (self.free_text and self.free_text.strip()) and not self.shots:
            raise ValueError("StoryInput requires free_text or at least one structured shot")
        return self


class ExecutionPolicy(StrictModel):
    mode: ExecutionMode = ExecutionMode.PLAN_ONLY
    allow_paid_video: bool = False
    max_shots: PositiveInt = 12
    max_search_calls: int = Field(default=8, ge=0)
    max_image_calls: int = Field(default=16, ge=0)
    max_video_calls: int = Field(default=0, ge=0)
    max_concurrency: PositiveInt = 4
    require_preview: bool = True

    @model_validator(mode="after")
    def enforce_paid_video_gate(self) -> ExecutionPolicy:
        if self.mode != ExecutionMode.FULL and self.allow_paid_video:
            raise ValueError("allow_paid_video is only valid when mode='full'")
        if self.allow_paid_video and self.max_video_calls == 0:
            raise ValueError("Paid video requires max_video_calls > 0")
        return self


class ProviderDescriptor(StrictModel):
    name: str
    model: str
    revision: str | None = None
    endpoint_kind: str | None = None


class PlannedReference(StrictModel):
    order: PositiveInt
    reference_id: SafeId
    role: str
    provenance: Provenance
    source_asset_id: SafeId | None = None
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    description: str | None = None


class VisualBible(StrictModel):
    style_prompt: str
    characters: list[dict[str, Any]] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    props: list[dict[str, Any]] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)


class PlannedAsset(StrictModel):
    asset_id: SafeId
    kind: Literal["style", "character", "location", "prop", "shot_keyframe"]
    role: str
    planned_prompt: str
    actual_prompt: str
    input_mode: InputMode
    references: list[PlannedReference] = Field(default_factory=list, max_length=5)
    dependencies: list[SafeId] = Field(default_factory=list)
    search_query: str | None = None
    visual_search_query: str | None = None
    generation_provider: str = "openai"
    output_path: str | None = None

    @model_validator(mode="after")
    def validate_input_mode(self) -> PlannedAsset:
        if self.input_mode == InputMode.TEXT and self.references:
            raise ValueError("text assets cannot contain reference images")
        if self.input_mode == InputMode.TEXT_IMAGE and not self.references:
            raise ValueError("text+image assets require at least one reference image")
        return self

    @property
    def prompt_sha256(self) -> str:
        return sha256_json({"prompt": self.actual_prompt})


class PlannedShot(StrictModel):
    shot_id: SafeId
    order: PositiveInt
    title: str | None = None
    original_prompt: str
    image_prompt: str
    h3_prompt: str
    duration_seconds: float = Field(default=10.0, gt=0, le=30)
    previous_shot_id: SafeId | None = None
    references: list[PlannedReference] = Field(default_factory=list, max_length=5)
    dependencies: list[SafeId] = Field(default_factory=list)
    keyframe_asset_id: SafeId
    metadata: dict[str, Any] = Field(default_factory=dict)


class CallEstimate(StrictModel):
    planning_calls: int = Field(ge=0)
    fact_search_calls: int = Field(ge=0)
    visual_search_calls: int = Field(ge=0)
    image_generation_calls: int = Field(ge=0)
    video_generation_calls: int = Field(ge=0)
    paid_video_locked: bool = True


class CanvasPlan(StrictModel):
    schema_version: Literal["storycanvas/v1"] = SCHEMA_VERSION
    plan_id: SafeId
    input_kind: Literal["shot", "story"]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_input: dict[str, Any]
    title: str
    story_id: str
    planning_provider: ProviderDescriptor
    fact_search_provider: ProviderDescriptor | None = None
    visual_search_provider: ProviderDescriptor | None = None
    image_provider: ProviderDescriptor | None = None
    video_provider: ProviderDescriptor | None = None
    visual_bible: VisualBible
    shared_assets: list[PlannedAsset] = Field(default_factory=list)
    shots: list[PlannedShot] = Field(min_length=1)
    call_estimate: CallEstimate
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> CanvasPlan:
        shot_ids = [shot.shot_id for shot in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("shot_id values must be unique")
        orders = [shot.order for shot in self.shots]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("shot orders must be contiguous and one-indexed")
        asset_ids = {asset.asset_id for asset in self.shared_assets}
        asset_ids.update(shot.keyframe_asset_id for shot in self.shots)
        for shot in self.shots:
            missing = set(shot.dependencies) - asset_ids
            if missing:
                raise ValueError(f"shot {shot.shot_id} has missing dependencies: {sorted(missing)}")
            if shot.previous_shot_id and shot.previous_shot_id not in shot_ids:
                raise ValueError(f"shot {shot.shot_id} references unknown previous shot")
        return self


class WorkflowNodeSummary(StrictModel):
    node_id: str
    class_type: str
    title: str
    shot_id: str | None = None


class CompiledWorkflow(StrictModel):
    schema_version: Literal["storycanvas/v1"] = SCHEMA_VERSION
    plan_id: str
    workflow: dict[str, Any]
    api_workflow: dict[str, Any]
    node_index: list[WorkflowNodeSummary]
    workflow_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[str] = Field(default_factory=list)


class AttemptRecord(StrictModel):
    attempt: PositiveInt
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["running", "success", "failed", "rejected"]
    error_type: str | None = None
    error_message: str | None = None


class ProviderReceipt(StrictModel):
    provider: str
    model: str
    operation: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_id: str | None = None
    task_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(StrictModel):
    artifact_id: SafeId
    kind: Literal["image", "video", "json", "html", "text"]
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Provenance | None = None
    prompt: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_mode: InputMode | None = None
    ordered_references: list[PlannedReference] = Field(default_factory=list)
    receipt: ProviderReceipt | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunManifest(StrictModel):
    schema_version: Literal["storycanvas/v1"] = SCHEMA_VERSION
    run_id: SafeId
    plan_id: SafeId
    status: RunStatus
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: ExecutionPolicy
    run_root: str
    composition_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plugins: list[SafeId] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    call_counts: dict[str, int] = Field(default_factory=dict)
    estimated_cost: dict[str, float | str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class RunRecord(StrictModel):
    run_id: str
    root: Path
    manifest: RunManifest
    plan: CanvasPlan
    compiled_workflow: CompiledWorkflow
