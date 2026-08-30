from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from pydantic import Field

from ..errors import ProviderError
from ..schemas import (
    CallEstimate,
    CanvasPlan,
    ExecutionPolicy,
    InputMode,
    PlannedAsset,
    PlannedReference,
    PlannedShot,
    Provenance,
    ProviderDescriptor,
    ShotInput,
    StoryInput,
    StrictModel,
    VisualBible,
)
from ..utils import sha256_json, slugify

DIRECTOR_SYSTEM_PROMPT = """You are StoryCanvas Director, a planning agent for auditable multi-reference video workflows.
Return only the requested typed plan. You plan story structure, reusable visual assets, exact image prompts,
ordered visual dependencies, and six-part 10-second video prompts. You do not output ComfyUI nodes or arbitrary
workflow JSON. Never invent a web source. Mark only real-world facts that need search. Prefer explicit continuity
rules over mechanically chaining every shot. A shot may use its previous shot only when the place and visible state
should persist. Image prompts must be self-contained and must state composition, identity, state, lighting, and style.
H3 prompts must describe subject, scene, camera, 0-10 second action, continuity, and sound. Use search_query for
textual factual grounding. Use visual_search_query only when a real visual exemplar is genuinely needed."""


class DraftAsset(StrictModel):
    asset_id: str
    kind: Literal["style", "character", "location", "prop"]
    role: str
    prompt: str
    search_query: str | None = None
    visual_search_query: str | None = None


class DraftShot(StrictModel):
    title: str | None = None
    original_prompt: str
    image_prompt: str
    h3_prompt: str
    use_previous_shot: bool = False
    shared_asset_ids: list[str] = Field(default_factory=list, max_length=4)


class DraftVisualEntity(StrictModel):
    entity_id: str
    name: str
    description: str
    state: str | None = None


class DirectorDraft(StrictModel):
    title: str
    style_prompt: str
    characters: list[DraftVisualEntity] = Field(default_factory=list)
    locations: list[DraftVisualEntity] = Field(default_factory=list)
    props: list[DraftVisualEntity] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    shared_assets: list[DraftAsset] = Field(default_factory=list)
    shots: list[DraftShot] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


def _split_story(value: str, maximum: int) -> list[str]:
    parts = [
        part.strip() for part in re.split(r"(?:\n+|(?<=[.!?。！？])\s+)", value) if part.strip()
    ]
    if not parts:
        return [value.strip()]
    return parts[:maximum]


def _user_shots(value: ShotInput | StoryInput, policy: ExecutionPolicy) -> list[ShotInput]:
    if isinstance(value, ShotInput):
        return [value]
    if value.shots:
        return value.shots[: policy.max_shots]
    if value.free_text is None:
        raise ValueError("StoryInput must contain free_text or explicit shots")
    return [
        ShotInput(shot_id=f"shot-{index:03d}", prompt=prompt)
        for index, prompt in enumerate(_split_story(value.free_text, policy.max_shots), start=1)
    ]


def _all_user_references(value: ShotInput | StoryInput, shot: ShotInput) -> list[Any]:
    story_references = value.references if isinstance(value, StoryInput) else []
    merged = [*shot.references, *story_references]
    seen: set[str] = set()
    result = []
    for reference in merged:
        if reference.reference_id not in seen:
            result.append(reference)
            seen.add(reference.reference_id)
    if shot.fixed_reference_plan:
        rank = {reference_id: index for index, reference_id in enumerate(shot.fixed_reference_plan)}
        result = sorted(
            (item for item in result if item.reference_id in rank),
            key=lambda item: rank[item.reference_id],
        )
    return result


def draft_to_plan(
    value: ShotInput | StoryInput,
    policy: ExecutionPolicy,
    draft: DirectorDraft,
    provider: ProviderDescriptor,
    *,
    fact_search_provider: ProviderDescriptor | None = None,
    visual_search_provider: ProviderDescriptor | None = None,
    image_provider: ProviderDescriptor | None = None,
    video_provider: ProviderDescriptor | None = None,
) -> CanvasPlan:
    source_shots = _user_shots(value, policy)
    if len(draft.shots) != len(source_shots):
        raise ProviderError(
            f"Director returned {len(draft.shots)} shots for {len(source_shots)} requested shots"
        )
    story_id = (
        (value.story_id if isinstance(value, StoryInput) else None)
        or (value.shot_id if isinstance(value, ShotInput) else None)
        or slugify(draft.title)
    )
    input_kind = "shot" if isinstance(value, ShotInput) else "story"
    input_sha = sha256_json(value)
    plan_id = f"plan-{input_sha[:16]}"

    shared_assets: list[PlannedAsset] = []
    for item in draft.shared_assets:
        shared_assets.append(
            PlannedAsset(
                asset_id=item.asset_id,
                kind=item.kind,
                role=item.role,
                planned_prompt=item.prompt,
                actual_prompt=item.prompt,
                input_mode=InputMode.TEXT,
                search_query=item.search_query,
                visual_search_query=item.visual_search_query,
                generation_provider=(image_provider.name if image_provider else "openai"),
            )
        )
    shared_by_id = {asset.asset_id: asset for asset in shared_assets}

    shots: list[PlannedShot] = []
    for index, (source, planned) in enumerate(zip(source_shots, draft.shots, strict=True), start=1):
        shot_id = source.shot_id or f"shot-{index:03d}"
        references: list[PlannedReference] = []
        for user_reference in _all_user_references(value, source):
            references.append(
                PlannedReference(
                    order=len(references) + 1,
                    reference_id=user_reference.reference_id,
                    role=user_reference.role,
                    provenance=Provenance.USER_REFERENCE,
                    path=user_reference.path,
                    sha256=user_reference.sha256,
                    description=user_reference.description,
                )
            )
        previous_shot_id = None
        dependencies: list[str] = []
        if planned.use_previous_shot and index > 1 and len(references) < 5:
            previous_shot = shots[-1]
            previous_shot_id = previous_shot.shot_id
            dependencies.append(previous_shot.keyframe_asset_id)
            references.append(
                PlannedReference(
                    order=len(references) + 1,
                    reference_id=f"previous-{previous_shot.shot_id}",
                    role="previous_shot",
                    provenance=Provenance.PREVIOUS_SHOT,
                    source_asset_id=previous_shot.keyframe_asset_id,
                    description="Previous keyframe for explicit same-scene continuity",
                )
            )
        for asset_id in planned.shared_asset_ids:
            if len(references) >= 5:
                break
            if asset_id not in shared_by_id:
                raise ProviderError(f"Director referenced unknown shared asset: {asset_id}")
            asset = shared_by_id[asset_id]
            dependencies.append(asset_id)
            references.append(
                PlannedReference(
                    order=len(references) + 1,
                    reference_id=f"asset-{asset_id}",
                    role=asset.role,
                    provenance=Provenance.IMAGE_GENERATION,
                    source_asset_id=asset_id,
                    description=f"Generated reusable {asset.kind} anchor",
                )
            )
        keyframe_asset_id = f"{shot_id}-keyframe"
        shots.append(
            PlannedShot(
                shot_id=shot_id,
                order=index,
                title=planned.title or source.title,
                original_prompt=source.prompt,
                image_prompt=planned.image_prompt,
                h3_prompt=planned.h3_prompt,
                previous_shot_id=previous_shot_id,
                references=references,
                dependencies=dependencies,
                keyframe_asset_id=keyframe_asset_id,
                metadata=source.metadata,
            )
        )

    search_calls = sum(asset.search_query is not None for asset in shared_assets)
    visual_search_calls = sum(asset.visual_search_query is not None for asset in shared_assets)
    image_calls = len(shared_assets) + len(shots)
    video_calls = len(shots)
    warnings = list(draft.warnings)
    if image_calls > policy.max_image_calls:
        warnings.append(
            f"Plan needs {image_calls} image calls but policy allows {policy.max_image_calls}."
        )
    if search_calls + visual_search_calls > policy.max_search_calls:
        warnings.append(
            f"Plan needs {search_calls + visual_search_calls} search calls but policy allows "
            f"{policy.max_search_calls}."
        )
    if video_calls > policy.max_video_calls:
        warnings.append(
            f"Plan contains {video_calls} video calls but policy allows {policy.max_video_calls}."
        )
    if not policy.allow_paid_video:
        warnings.append("Paid video generation is locked until explicitly enabled.")

    return CanvasPlan(
        plan_id=plan_id,
        input_kind=input_kind,
        input_sha256=input_sha,
        source_input=value.model_dump(mode="json", exclude_none=True),
        title=draft.title,
        story_id=story_id,
        planning_provider=provider,
        fact_search_provider=fact_search_provider
        or ProviderDescriptor(name="openai", model="web_search", endpoint_kind="responses"),
        visual_search_provider=visual_search_provider
        or ProviderDescriptor(name="serper", model="images", endpoint_kind="optional"),
        image_provider=image_provider
        or ProviderDescriptor(
            name="openai",
            model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            endpoint_kind="images",
        ),
        video_provider=video_provider
        or ProviderDescriptor(
            name="minimax-h3-compatible",
            model=os.getenv("MINIMAX_H3_MODEL", "MiniMax-H3"),
            endpoint_kind="async-http",
        ),
        visual_bible=VisualBible(
            style_prompt=draft.style_prompt,
            characters=[item.model_dump(mode="json") for item in draft.characters],
            locations=[item.model_dump(mode="json") for item in draft.locations],
            props=[item.model_dump(mode="json") for item in draft.props],
            continuity_rules=draft.continuity_rules,
        ),
        shared_assets=shared_assets,
        shots=shots,
        call_estimate=CallEstimate(
            planning_calls=1,
            fact_search_calls=search_calls,
            visual_search_calls=visual_search_calls,
            image_generation_calls=image_calls,
            video_generation_calls=video_calls,
            paid_video_locked=not policy.allow_paid_video,
        ),
        warnings=warnings,
    )


class DeterministicDirector:
    """Offline planner used by tests, examples, and no-key workflow previews."""

    name = "deterministic"
    model = "storycanvas-rule-director-v1"

    def plan(self, value: ShotInput | StoryInput, policy: ExecutionPolicy) -> CanvasPlan:
        source_shots = _user_shots(value, policy)
        title = (
            value.title if isinstance(value, StoryInput) else value.title
        ) or "Untitled StoryCanvas"
        style_asset = DraftAsset(
            asset_id="style-bible",
            kind="style",
            role="style_bible",
            prompt=(
                "A clean cinematic visual style anchor with coherent palette, natural volumetric lighting, "
                "production design, and no people, characters, logos, captions, or watermarks."
            ),
        )
        draft_shots = []
        for index, source in enumerate(source_shots, start=1):
            draft_shots.append(
                DraftShot(
                    title=source.title or f"Shot {index}",
                    original_prompt=source.prompt,
                    image_prompt=(
                        f"Create a cinematic 16:9 keyframe for: {source.prompt}. Preserve the supplied identity, "
                        "wardrobe, location, prop state, and visual style. Clear subject staging, readable action, "
                        "natural light, no text, no watermark."
                    ),
                    h3_prompt=(
                        f"Subject and scene: {source.prompt}\n"
                        "Camera: stable cinematic composition with one motivated movement.\n"
                        "0-3s: establish the subject, location, and current prop state.\n"
                        "3-7s: perform the central action with physically coherent motion.\n"
                        "7-10s: resolve the action while preserving identity and scene continuity.\n"
                        "Sound: natural synchronized ambience and action sounds; no narration unless requested."
                    ),
                    use_previous_shot=index > 1,
                    shared_asset_ids=["style-bible"],
                )
            )
        draft = DirectorDraft(
            title=title,
            style_prompt=style_asset.prompt,
            continuity_rules=[
                "Keep recurring character identity, wardrobe, and proportions stable.",
                "Carry visible prop and environment state only across shots that explicitly depend on it.",
            ],
            shared_assets=[style_asset],
            shots=draft_shots,
            warnings=[
                "Offline deterministic preview: replace with the OpenAI Director for production planning."
            ],
        )
        return draft_to_plan(
            value,
            policy,
            draft,
            ProviderDescriptor(name=self.name, model=self.model, endpoint_kind="offline"),
        )


class OpenAIDirector:
    name = "openai"

    def __init__(self, *, model: str | None = None, client: Any | None = None):
        self.model: str = model or os.environ.get("OPENAI_TEXT_MODEL", "gpt-5")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:  # pragma: no cover - import error is environment-specific
                raise ProviderError("Install the openai package to use OpenAIDirector") from error
            client = OpenAI()
        self.client: Any = client

    def plan(self, value: ShotInput | StoryInput, policy: ExecutionPolicy) -> CanvasPlan:
        payload = {
            "input_kind": "shot" if isinstance(value, ShotInput) else "story",
            "input": value.model_dump(mode="json", exclude_none=True),
            "limits": {
                "max_shots": policy.max_shots,
                "max_references_per_image": 5,
                "video_duration_seconds": 10,
            },
        }
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=DIRECTOR_SYSTEM_PROMPT,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=DirectorDraft,
            )
            draft = response.output_parsed
            if draft is None:
                raise ProviderError("OpenAI Director returned no structured plan")
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                f"OpenAI Director failed: {type(error).__name__}: {error}"
            ) from error
        return draft_to_plan(
            value,
            policy,
            draft,
            ProviderDescriptor(name=self.name, model=self.model, endpoint_kind="responses.parse"),
        )
