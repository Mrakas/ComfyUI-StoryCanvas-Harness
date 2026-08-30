from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from storycanvas_harness.providers.codex import (
    CodexDirector,
    CodexImageProvider,
    CodexTurnResult,
)
from storycanvas_harness.providers.director import DirectorDraft, DraftAsset, DraftShot
from storycanvas_harness.schemas import ExecutionPolicy, Provenance, ShotInput, StoryInput


class FakeCodexClient:
    model = "gpt-5.6-sol"
    reasoning_effort = "medium"

    def __init__(self, results: list[CodexTurnResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> CodexTurnResult:
        self.calls.append(kwargs)
        return self.results.pop(0)


def _turn(*, response: str = "", items: list[dict[str, object]] | None = None) -> CodexTurnResult:
    return CodexTurnResult(
        thread_id="thread-test",
        turn_id="turn-test",
        status="completed",
        final_response=response,
        items=list(items or []),
        duration_ms=123,
        usage={},
        account_type="chatgpt",
        account_plan="pro",
        runtime_version="codex-cli 0.146.0",
    )


def test_codex_director_records_login_model_effort_and_fixed_dag(tmp_path: Path) -> None:
    draft = DirectorDraft(
        title="Moon Garden",
        style_prompt="Moonlit glasshouse style.",
        continuity_rules=["Keep the botanist, pot, notebook and glasshouse consistent."],
        shared_assets=[
            DraftAsset(
                asset_id="style-bible",
                kind="style",
                role="style_bible",
                prompt="Moonlit glasshouse style with blue and silver palette, no people.",
            )
        ],
        shots=[
            DraftShot(
                original_prompt="Plant the silver seed.",
                image_prompt="The botanist plants a silver seed beside a red notebook.",
                h3_prompt="Subject and scene...",
                shared_asset_ids=["style-bible"],
            ),
            DraftShot(
                original_prompt="The seed becomes a vine.",
                image_prompt="The same seed becomes a blue vine around the clay pot.",
                h3_prompt="Subject and scene...",
                use_previous_shot=True,
                shared_asset_ids=["style-bible"],
            ),
            DraftShot(
                original_prompt="Three moths emerge.",
                image_prompt="The same botanist raises the pot and exactly three moths emerge.",
                h3_prompt="Subject and scene...",
                use_previous_shot=True,
                shared_asset_ids=["style-bible"],
            ),
        ],
    )
    client = FakeCodexClient([_turn(response=draft.model_dump_json())])
    director = CodexDirector(client=client, cwd=tmp_path)  # type: ignore[arg-type]
    value = StoryInput(
        title="Moon Garden",
        shots=[
            ShotInput(shot_id="garden-01", prompt="Plant the silver seed."),
            ShotInput(shot_id="garden-02", prompt="The seed becomes a vine."),
            ShotInput(shot_id="garden-03", prompt="Three moths emerge."),
        ],
    )
    plan = director.plan(value, ExecutionPolicy(max_shots=3, max_image_calls=4))
    assert plan.planning_provider.model == "gpt-5.6-sol"
    assert "chatgpt-login" in str(plan.planning_provider.endpoint_kind)
    assert "reasoning=medium" in str(plan.planning_provider.endpoint_kind)
    assert plan.image_provider is not None
    assert plan.image_provider.name == "codex-app-server"
    assert len(plan.shared_assets) == 1
    assert len(plan.shots) == 3
    assert [item.provenance for item in plan.shots[1].references] == [
        Provenance.IMAGE_GENERATION,
        Provenance.PREVIOUS_SHOT,
    ]


def test_codex_image_provider_uses_ordered_local_images_and_records_receipt(
    tmp_path: Path,
) -> None:
    reference_one = tmp_path / "style.png"
    reference_two = tmp_path / "previous.png"
    Image.new("RGB", (32, 32), "navy").save(reference_one)
    Image.new("RGB", (32, 32), "purple").save(reference_two)
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), "blue").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    client = FakeCodexClient(
        [
            _turn(
                items=[
                    {
                        "type": "imageGeneration",
                        "id": "image-item-1",
                        "status": "completed",
                        "result": encoded,
                        "revised_prompt": "Actual native image prompt",
                    }
                ]
            )
        ]
    )
    provider = CodexImageProvider(client=client)  # type: ignore[arg-type]
    destination = tmp_path / "output" / "shot.png"
    generated = provider.generate("Planned prompt", [reference_one, reference_two], destination)
    assert generated.path == destination
    with Image.open(destination) as image:
        assert image.size == (1344, 768)
    call = client.calls[0]
    assert call["references"] == [reference_one, reference_two]
    assert generated.receipt.provider == "codex-app-server"
    assert generated.receipt.metadata["auth_type"] == "chatgpt"
    assert generated.receipt.metadata["thread_id"] == "thread-test"
    assert generated.receipt.metadata["turn_id"] == "turn-test"
    assert generated.receipt.metadata["item_id"] == "image-item-1"
    assert generated.metadata["actual_prompt"] == "Actual native image prompt"
