from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from ..schemas import (
    CanvasPlan,
    ExecutionPolicy,
    ProviderReceipt,
    ShotInput,
    StoryInput,
    StrictModel,
)


class SearchHit(StrictModel):
    title: str
    url: str
    image_url: str | None = None
    publisher: str | None = None
    snippet: str | None = None


class SearchResult(StrictModel):
    query: str
    summary: str
    hits: list[SearchHit] = Field(default_factory=list)
    receipt: ProviderReceipt


class GeneratedFile(StrictModel):
    path: Path
    receipt: ProviderReceipt
    metadata: dict[str, Any] = Field(default_factory=dict)


class DirectorProvider(Protocol):
    name: str
    model: str

    def plan(self, value: ShotInput | StoryInput, policy: ExecutionPolicy) -> CanvasPlan: ...


class FactSearchProvider(Protocol):
    name: str
    model: str

    def search(self, query: str) -> SearchResult: ...


class VisualSearchProvider(Protocol):
    name: str
    model: str

    def search(self, query: str) -> SearchResult: ...


class ImageProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, references: list[Path], destination: Path) -> GeneratedFile: ...


class VideoProvider(Protocol):
    name: str
    model: str

    def generate(
        self,
        prompt: str,
        references: list[Path],
        destination: Path,
        state_path: Path,
    ) -> GeneratedFile: ...
