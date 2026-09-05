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
    # Transport fixture bytes are intentionally not a real MP4; media decoding has its own integration tests.
    monkeypatch.setattr("storycanvas_harness.providers.video.full_decode", lambda path: None)
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


@pytest.mark.parametrize("status", ["creating", "create_ambiguous"])
def test_process_exit_during_create_never_submits_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from storycanvas_harness.utils import sha256_file, sha256_json

    reference = tmp_path / "reference.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(reference)
    state_path = tmp_path / "task.json"
    identity = {
        "model": "MiniMax-H3",
        "prompt": "test prompt",
        "references": [{"path": reference.name, "sha256": sha256_file(reference)}],
        "resolution": "768P",
        "duration": 10,
        "ratio": "16:9",
    }
    state_path.write_text(json.dumps({"status": status, "request_sha256": sha256_json(identity)}))
    provider = MiniMaxH3CompatibleProvider(api_key="test-only", base_url="https://minimax.invalid")
    monkeypatch.setattr(
        provider, "_upload", lambda path: pytest.fail("Must reconcile before uploads")
    )
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: pytest.fail("Must not create a duplicate paid task")
    )
    with pytest.raises(ResumeConflict, match="manual reconciliation"):
        provider.generate("test prompt", [reference], tmp_path / "video.mp4", state_path)


def test_corrupted_cached_video_downloads_from_existing_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(reference)
    destination = tmp_path / "video.mp4"
    state_path = tmp_path / "task.json"
    provider = MiniMaxH3CompatibleProvider(
        api_key="test-only", base_url="https://minimax.invalid", poll_interval=0
    )
    creates = []
    polls = []
    monkeypatch.setattr(provider, "_upload", lambda path: "mm_file://123")

    def create(*args, **kwargs):
        creates.append(1)
        return FakeResponse({"task_id": "same-task"})

    def poll(url, **kwargs):
        polls.append(url)
        return FakeResponse(
            {"task": {"status": "success", "content": {"url": "https://cdn.invalid/video"}}}
        )

    monkeypatch.setattr(httpx, "post", create)
    monkeypatch.setattr(httpx, "get", poll)
    monkeypatch.setattr(
        provider, "_download", lambda url, path: path.write_bytes(b"transport-fixture")
    )
    monkeypatch.setattr("storycanvas_harness.providers.video.full_decode", lambda path: None)
    provider.generate("test prompt", [reference], destination, state_path)
    destination.write_bytes(b"corrupted")
    result = provider.generate("test prompt", [reference], destination, state_path)
    assert len(creates) == 1
    assert len(polls) == 2
    assert all(url.endswith("same-task") for url in polls)
    assert destination.read_bytes() == b"transport-fixture"
    assert result.metadata["task_created"] is False
