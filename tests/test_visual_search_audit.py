from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.errors import ProviderError
from storycanvas_harness.providers.base import SearchHit, SearchResult
from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.providers.image import MockImageProvider
from storycanvas_harness.schemas import (
    ExecutionMode,
    ExecutionPolicy,
    Provenance,
    ProviderReceipt,
    ShotInput,
)
from storycanvas_harness.utils import sha256_json


class FakeVisualSearch:
    name = "fake-visual"
    model = "fake-v1"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str) -> SearchResult:
        self.calls += 1
        return SearchResult(
            query=query,
            summary="one fictional licensed exemplar",
            hits=[
                SearchHit(
                    title="Fictional source",
                    url="https://example.com/source",
                    image_url="https://images.example.com/source.png",
                    publisher="example.com",
                )
            ],
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="visual_search",
                request_sha256=sha256_json({"query": query}),
            ),
        )


def test_visual_search_is_a_separate_audited_reference_and_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = FakeVisualSearch()
    canvas = StoryCanvas(
        runs_dir=tmp_path / "runs",
        director=DeterministicDirector(),
        visual_search=search,
        image_provider=MockImageProvider(),
    )
    policy = ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=2, max_search_calls=1)
    plan = canvas.plan(ShotInput(prompt="A fictional brass observatory rotates."), policy)
    plan.shared_assets[0].visual_search_query = "fictional public-domain observatory reference"
    plan.call_estimate.visual_search_calls = 1

    def download(url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), (40, 80, 120)).save(destination)

    monkeypatch.setattr(StoryCanvas, "_download_public_image", staticmethod(download))
    first = canvas.run_plan(plan, policy)
    second = canvas.run_plan(plan, policy)

    assert first.manifest.status.value == "complete"
    assert search.calls == 1
    assert second.manifest.call_counts["visual_search"] == 0
    assert second.manifest.call_counts["visual_search_cache_hits"] == 1
    search_assets = [
        item for item in first.manifest.artifacts if item.provenance == Provenance.VISUAL_SEARCH
    ]
    assert len(search_assets) == 1
    rows = [
        json.loads(line)
        for line in (first.root / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    style = next(item for item in rows if item["asset_id"] == "style-bible")
    assert style["input_mode"] == "text+image"
    assert style["ordered_references"][0]["provenance"] == "visual_search"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/image.png",
        "https://127.0.0.1/image.png",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_visual_search_blocks_unsafe_urls(url: str) -> None:
    with pytest.raises(ProviderError):
        StoryCanvas._require_public_https(url)
