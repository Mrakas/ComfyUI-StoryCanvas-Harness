from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from storycanvas_harness.errors import ResumeConflict
from storycanvas_harness.providers.video import MiniMaxH3CompatibleProvider


class FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def test_minimax_task_id_is_persisted_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), (20, 40, 60)).save(reference)
    destination = tmp_path / "video.mp4"
    state_path = tmp_path / "task.json"
    provider = MiniMaxH3CompatibleProvider(
        api_key="test-only",
        base_url="https://minimax.invalid",
        poll_interval=0,
        poll_timeout=5,
    )
    calls = {"upload": 0, "create": 0, "poll": 0, "download": 0}

    def upload(path: Path) -> str:
        calls["upload"] += 1
        return "mm_file://123"

    def create(*args: Any, **kwargs: Any) -> FakeResponse:
        calls["create"] += 1
        return FakeResponse({"task_id": "task-123"})

    def poll(*args: Any, **kwargs: Any) -> FakeResponse:
        calls["poll"] += 1
        return FakeResponse(
            {"task": {"status": "success", "content": {"url": "https://cdn.invalid/v.mp4"}}}
        )

    def download(url: str, output: Path) -> None:
        calls["download"] += 1
        output.write_bytes(b"fictional-video")

    monkeypatch.setattr(provider, "_upload", upload)
    monkeypatch.setattr(provider, "_download", download)
    monkeypatch.setattr(httpx, "post", create)
    monkeypatch.setattr(httpx, "get", poll)

    first = provider.generate("test prompt", [reference], destination, state_path)
    second = provider.generate("test prompt", [reference], destination, state_path)

    assert first.receipt.task_id == "task-123"
    assert second.metadata["resumed"] is True
    assert calls == {"upload": 1, "create": 1, "poll": 1, "download": 1}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert state["task_id"] == "task-123"


def test_ambiguous_paid_creation_is_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), (20, 40, 60)).save(reference)
    state_path = tmp_path / "task.json"
    provider = MiniMaxH3CompatibleProvider(
        api_key="test-only", base_url="https://minimax.invalid", poll_interval=0
    )
    create_calls = 0
    monkeypatch.setattr(provider, "_upload", lambda path: "mm_file://123")

    def ambiguous(*args: Any, **kwargs: Any) -> FakeResponse:
        nonlocal create_calls
        create_calls += 1
        raise httpx.ReadTimeout("unknown create outcome")

    monkeypatch.setattr(httpx, "post", ambiguous)
    with pytest.raises(ResumeConflict, match="ambiguous"):
        provider.generate("test prompt", [reference], tmp_path / "video.mp4", state_path)
    with pytest.raises(ResumeConflict, match="Ambiguous paid task"):
        provider.generate("test prompt", [reference], tmp_path / "video.mp4", state_path)
    assert create_calls == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "create_ambiguous"
