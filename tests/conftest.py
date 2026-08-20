from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.providers.image import MockImageProvider
from storycanvas_harness.providers.search import MockFactSearch
from storycanvas_harness.providers.video import MockVideoProvider


@pytest.fixture
def reference_image(tmp_path: Path) -> Path:
    path = tmp_path / "reference.png"
    Image.new("RGB", (320, 240), (58, 92, 132)).save(path)
    return path


@pytest.fixture
def mock_canvas(tmp_path: Path) -> StoryCanvas:
    return StoryCanvas(
        runs_dir=tmp_path / "runs",
        director=DeterministicDirector(),
        fact_search=MockFactSearch(),
        image_provider=MockImageProvider(),
        video_provider=MockVideoProvider(),
    )
