from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

import httpx

from ..errors import ProviderError, ResumeConflict
from ..schemas import ProviderReceipt
from ..utils import atomic_write_json, sha256_file, sha256_json
from .base import GeneratedFile

# subprocess is required for fixed ffmpeg argv; shell execution is never used.

SUCCESS_STATUSES = {"success", "succeeded", "completed", "done"}
FAILED_STATUSES = {"failed", "fail", "cancelled", "canceled", "expired", "error"}


class MockVideoProvider:
    name = "mock"
    model = "mock-video-v1"

    def generate(
        self,
        prompt: str,
        references: list[Path],
        destination: Path,
        state_path: Path,
    ) -> GeneratedFile:
        request = {"prompt": prompt, "references": [path.name for path in references]}
        destination.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProviderError("ffmpeg is required by MockVideoProvider")
        command = [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x243447:s=1344x768:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(destination),
        ]
        # The executable is resolved locally and argv is fixed.
        result = subprocess.run(  # nosec B603
            command, capture_output=True, text=True, timeout=60, check=False
        )
        if result.returncode:
            raise ProviderError(f"Mock ffmpeg failed: {result.stderr[-1000:]}")
        state = {
            "status": "succeeded",
            "request_sha256": sha256_json(request),
            "task_id": "mock-task",
            "video_sha256": sha256_file(destination),
        }
        atomic_write_json(state_path, state)
        return GeneratedFile(
            path=destination,
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="video_generation",
                request_sha256=state["request_sha256"],
                task_id="mock-task",
            ),
            metadata={"mock": True, "duration_seconds": 1},
        )


class MiniMaxH3CompatibleProvider:
    """Resumable adapter for the public ComfyUI-MiniMaxH3-API-compatible protocol.

    A persisted task ID is authoritative. If task creation has an ambiguous transport
    failure, this provider refuses to retry because the paid task may already exist.
    """

    name = "minimax-h3-compatible"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        poll_interval: float = 10,
        poll_timeout: float = 7200,
    ):
        resolved_key = api_key or os.environ.get("MINIMAX_H3_API_KEY", "")
        resolved_url = base_url or os.environ.get("MINIMAX_H3_BASE_URL", "")
        if not resolved_key or not resolved_url:
            raise ProviderError("MINIMAX_H3_API_KEY and MINIMAX_H3_BASE_URL are required")
        self.api_key: str = resolved_key
        self.base_url: str = resolved_url.rstrip("/")
        self.model: str = model or os.environ.get("MINIMAX_H3_MODEL", "MiniMax-H3")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _upload(self, path: Path) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as stream:
            response = httpx.post(
                f"{self.base_url}/v1/files/upload",
                headers=self.headers,
                data={"purpose": "video_generation_input"},
                files={"file": (path.name, stream, content_type)},
                timeout=600,
            )
        response.raise_for_status()
        payload = response.json()
        base_resp = payload.get("base_resp") or {}
        if base_resp.get("status_code", 0) != 0:
            raise ProviderError(f"MiniMax upload rejected: {base_resp.get('status_msg')}")
        file_info = payload.get("file") or {}
        if file_info.get("file_id") is not None:
            return f"mm_file://{file_info['file_id']}"
        if file_info.get("download_url"):
            return str(file_info["download_url"])
        raise ProviderError("MiniMax upload returned no file_id or download_url")

    def _download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        with httpx.stream("GET", url, timeout=600, follow_redirects=True) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if not temporary.stat().st_size:
            raise ProviderError("Downloaded MiniMax video is empty")
        os.replace(temporary, destination)

    def generate(
        self,
        prompt: str,
        references: list[Path],
        destination: Path,
        state_path: Path,
    ) -> GeneratedFile:
        if not 1 <= len(references) <= 9:
            raise ProviderError("MiniMax-H3 requires 1-9 ordered reference images")
        reference_identity = [
            {"path": path.name, "sha256": sha256_file(path)} for path in references
        ]
        identity = {
            "model": self.model,
            "prompt": prompt,
            "references": reference_identity,
            "resolution": "768P",
            "duration": 10,
            "ratio": "16:9",
        }
        request_sha = sha256_json(identity)
        state: dict[str, Any] = {}
        if state_path.is_file():
            import json

            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("request_sha256") != request_sha:
                raise ResumeConflict("Persisted MiniMax task belongs to a different request")
            if state.get("status") == "create_ambiguous" and not state.get("task_id"):
                raise ResumeConflict("Ambiguous paid task creation requires manual reconciliation")
            if state.get("status") == "succeeded" and destination.is_file():
                return GeneratedFile(
                    path=destination,
                    receipt=ProviderReceipt(
                        provider=self.name,
                        model=self.model,
                        operation="video_generation",
                        request_sha256=request_sha,
                        task_id=str(state.get("task_id")),
                    ),
                    metadata={"resumed": True},
                )

        task_id = state.get("task_id")
        if not task_id:
            uploaded = [self._upload(path) for path in references]
            payload: dict[str, Any] = {
                "model": self.model,
                "content": [{"type": "text", "text": prompt}],
                "resolution": "768P",
                "duration": 10,
                "ratio": "16:9",
            }
            for uri in uploaded:
                payload["content"].append(
                    {
                        "type": "image_url",
                        "image_url": {"url": uri},
                        "role": "reference_image",
                    }
                )
            state = {"status": "creating", "request_sha256": request_sha}
            atomic_write_json(state_path, state)
            try:
                response = httpx.post(
                    f"{self.base_url}/v2/video_generation",
                    headers={**self.headers, "Content-Type": "application/json"},
                    json=payload,
                    timeout=600,
                )
                response.raise_for_status()
                task_id = str(response.json().get("task_id") or "")
                if not task_id:
                    raise ProviderError("MiniMax create response has no task_id")
            except Exception as error:
                state.update(status="create_ambiguous", error=f"{type(error).__name__}: {error}")
                atomic_write_json(state_path, state)
                raise ResumeConflict(
                    "MiniMax task creation was ambiguous; automatic retry is disabled"
                ) from error
            state.update(status="submitted", task_id=task_id)
            atomic_write_json(state_path, state)

        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    f"{self.base_url}/v2/query/video_generation/{task_id}",
                    headers=self.headers,
                    timeout=120,
                )
                response.raise_for_status()
                remote = response.json().get("task") or {}
            except Exception as error:
                state.update(
                    status="polling_interrupted", last_error=f"{type(error).__name__}: {error}"
                )
                atomic_write_json(state_path, state)
                time.sleep(self.poll_interval)
                continue
            status = str(remote.get("status") or "queued").casefold()
            state.update(status="polling", remote_status=status)
            video_url = (remote.get("content") or {}).get("url")
            if video_url:
                self._download(str(video_url), destination)
                state.update(
                    status="succeeded",
                    video_sha256=sha256_file(destination),
                )
                atomic_write_json(state_path, state)
                return GeneratedFile(
                    path=destination,
                    receipt=ProviderReceipt(
                        provider=self.name,
                        model=self.model,
                        operation="video_generation",
                        request_sha256=request_sha,
                        task_id=str(task_id),
                    ),
                    metadata={"duration_seconds": 10, "resolution": "768P", "ratio": "16:9"},
                )
            if status in FAILED_STATUSES:
                state.update(status="failed", error=str(remote.get("error") or status))
                atomic_write_json(state_path, state)
                raise ProviderError(f"MiniMax task failed: {state['error']}")
            if status in SUCCESS_STATUSES:
                state.update(status="failed", error="Task succeeded without video URL")
                atomic_write_json(state_path, state)
                raise ProviderError(state["error"])
            atomic_write_json(state_path, state)
            time.sleep(self.poll_interval)
        state.update(status="polling_interrupted", error="Poll timeout")
        atomic_write_json(state_path, state)
        raise ProviderError("MiniMax task polling timed out; resume will reuse the same task ID")
