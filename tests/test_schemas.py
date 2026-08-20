from __future__ import annotations

import pytest
from pydantic import ValidationError

from storycanvas_harness.schemas import (
    ExecutionMode,
    ExecutionPolicy,
    InputMode,
    PlannedAsset,
    PlannedReference,
    Provenance,
    StoryInput,
)


def test_story_requires_free_text_or_shots() -> None:
    with pytest.raises(ValidationError):
        StoryInput()


def test_paid_video_requires_full_mode_and_budget() -> None:
    with pytest.raises(ValidationError):
        ExecutionPolicy(mode=ExecutionMode.ASSETS, allow_paid_video=True, max_video_calls=1)
    with pytest.raises(ValidationError):
        ExecutionPolicy(mode=ExecutionMode.FULL, allow_paid_video=True, max_video_calls=0)
    policy = ExecutionPolicy(mode=ExecutionMode.FULL, allow_paid_video=True, max_video_calls=2)
    assert policy.allow_paid_video


def test_text_asset_cannot_hide_image_inputs() -> None:
    reference = PlannedReference(
        order=1,
        reference_id="ref",
        role="identity",
        provenance=Provenance.USER_REFERENCE,
        path="ref.png",
    )
    with pytest.raises(ValidationError):
        PlannedAsset(
            asset_id="asset",
            kind="character",
            role="identity",
            planned_prompt="prompt",
            actual_prompt="prompt",
            input_mode=InputMode.TEXT,
            references=[reference],
        )


def test_text_image_asset_requires_reference() -> None:
    with pytest.raises(ValidationError):
        PlannedAsset(
            asset_id="asset",
            kind="character",
            role="identity",
            planned_prompt="prompt",
            actual_prompt="prompt",
            input_mode=InputMode.TEXT_IMAGE,
        )
