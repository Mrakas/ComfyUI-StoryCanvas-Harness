from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from PIL import Image

from ..errors import ProviderError
from ..schemas import (
    CanvasPlan,
    ExecutionPolicy,
    Provenance,
    ProviderDescriptor,
    ProviderReceipt,
    ShotInput,
    StoryInput,
)
from ..utils import sha256_json
from .base import GeneratedFile
from .director import DIRECTOR_SYSTEM_PROMPT, DirectorDraft, draft_to_plan


@dataclass(frozen=True, slots=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str
    status: str
    final_response: str
    items: list[dict[str, Any]]
    duration_ms: int | None
    usage: dict[str, Any]
    account_type: str
    account_plan: str | None
    runtime_version: str


class CodexAppServerClient:
    """Small, auditable adapter around the official local Codex Python SDK.

    The SDK launches ``codex app-server`` and reuses the already authenticated
    local Codex session. This adapter never opens or copies ``auth.json``.
    A fresh ephemeral thread is used for every operation so unrelated project
    context cannot leak into a generation request.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        codex_bin: str | None = None,
    ) -> None:
        self.model: str = model or os.getenv("STORYCANVAS_CODEX_MODEL") or "gpt-5.6-sol"
        self.reasoning_effort: str = (
            reasoning_effort or os.getenv("STORYCANVAS_CODEX_REASONING_EFFORT") or "medium"
        )
        selected_codex_bin = (
            codex_bin or os.getenv("STORYCANVAS_CODEX_BIN") or shutil.which("codex")
        )
        if not selected_codex_bin:
            raise ProviderError("Codex CLI was not found; set STORYCANVAS_CODEX_BIN")
        self.codex_bin: str = selected_codex_bin
        self._lock = Lock()
        self._capability: dict[str, Any] | None = None

    @property
    def runtime_version(self) -> str:
        try:
            # The executable is an explicit local configuration and argv is fixed;
            # no shell parsing or user-supplied arguments are used.
            completed = subprocess.run(  # nosec B603
                [self.codex_bin, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ProviderError(f"Could not read Codex CLI version: {error}") from error
        return completed.stdout.strip() or completed.stderr.strip() or "unknown"

    @staticmethod
    def _unwrap_item(item: Any) -> Any:
        return getattr(item, "root", item)

    @classmethod
    def _serialize_item(cls, item: Any) -> dict[str, Any]:
        raw = cls._unwrap_item(item)
        if hasattr(raw, "model_dump"):
            return dict(raw.model_dump(mode="json", by_alias=False, exclude_none=True))
        if isinstance(raw, dict):
            return dict(raw)
        return {"type": type(raw).__name__, "value": str(raw)}

    def _open(self, cwd: Path) -> Any:
        try:
            from openai_codex import Codex, CodexConfig
        except ImportError as error:  # pragma: no cover - environment specific
            raise ProviderError(
                "Install the codex extra with `uv sync --extra codex` to use Codex login mode"
            ) from error
        return Codex(
            config=CodexConfig(
                codex_bin=self.codex_bin,
                cwd=str(cwd),
                client_name="storycanvas_harness",
                client_title="StoryCanvas Harness",
                experimental_api=True,
            )
        )

    def capability(self, cwd: Path) -> dict[str, Any]:
        with self._lock:
            if self._capability is not None:
                return dict(self._capability)
            cwd.mkdir(parents=True, exist_ok=True)
            try:
                with self._open(cwd) as codex:
                    account_response = codex.account()
                    account = getattr(account_response, "account", None)
                    account_root = getattr(account, "root", account)
                    account_type = getattr(account_root, "type", None)
                    if account_type != "chatgpt":
                        raise ProviderError(
                            "Codex is not authenticated with ChatGPT; run `codex login` first"
                        )
                    models = list(getattr(codex.models(include_hidden=True), "data", []))
                    selected = next(
                        (
                            item
                            for item in models
                            if getattr(item, "id", None) == self.model
                            or getattr(item, "model", None) == self.model
                        ),
                        None,
                    )
                    if selected is None:
                        raise ProviderError(f"Codex model is unavailable: {self.model}")
                    efforts = {
                        str(getattr(getattr(item, "reasoning_effort", None), "value", ""))
                        for item in getattr(selected, "supported_reasoning_efforts", [])
                    }
                    if self.reasoning_effort not in efforts:
                        raise ProviderError(
                            f"Codex model {self.model} does not support effort "
                            f"{self.reasoning_effort}"
                        )
                    plan_type = getattr(account_root, "plan_type", None)
                    self._capability = {
                        "account_type": "chatgpt",
                        "account_plan": getattr(plan_type, "value", plan_type),
                        "model": self.model,
                        "reasoning_effort": self.reasoning_effort,
                        "runtime_version": self.runtime_version,
                        "input_modalities": [
                            getattr(item, "value", str(item))
                            for item in getattr(selected, "input_modalities", [])
                        ],
                    }
            except ProviderError:
                raise
            except Exception as error:
                raise ProviderError(
                    f"Codex capability check failed: {type(error).__name__}: {error}"
                ) from error
            return dict(self._capability)

    def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        references: list[Path] | None = None,
        output_schema: dict[str, Any] | None = None,
        base_instructions: str | None = None,
        workspace_write: bool = False,
    ) -> CodexTurnResult:
        references = references or []
        missing = [str(path) for path in references if not path.is_file()]
        if missing:
            raise ProviderError(f"Missing Codex localImage references: {missing}")
        cwd.mkdir(parents=True, exist_ok=True)
        capability = self.capability(cwd)
        try:
            from openai_codex import ApprovalMode, LocalImageInput, Sandbox, TextInput
            from openai_codex.types import ReasoningEffort
        except ImportError as error:  # pragma: no cover - environment specific
            raise ProviderError("The installed openai-codex SDK is incomplete") from error

        turn_input: list[Any] = [TextInput(prompt)]
        turn_input.extend(LocalImageInput(str(path.resolve())) for path in references)
        sandbox = Sandbox.workspace_write if workspace_write else Sandbox.read_only
        with self._lock:
            try:
                with self._open(cwd) as codex:
                    thread = codex.thread_start(
                        approval_mode=ApprovalMode.deny_all,
                        base_instructions=base_instructions,
                        cwd=str(cwd),
                        ephemeral=True,
                        model=self.model,
                        sandbox=sandbox,
                        service_name="storycanvas_harness",
                    )
                    result = thread.run(
                        turn_input,
                        approval_mode=ApprovalMode.deny_all,
                        cwd=str(cwd),
                        effort=ReasoningEffort(self.reasoning_effort),
                        model=self.model,
                        output_schema=output_schema,
                        sandbox=sandbox,
                    )
            except Exception as error:
                raise ProviderError(
                    f"Codex app-server turn failed: {type(error).__name__}: {error}"
                ) from error
        status = getattr(getattr(result, "status", None), "value", result.status)
        if status != "completed":
            detail = getattr(result, "error", None)
            raise ProviderError(f"Codex turn ended with status={status}: {detail}")
        usage = getattr(result, "usage", None)
        usage_payload: dict[str, Any] = {}
        if usage is not None and hasattr(usage, "model_dump"):
            usage_payload = dict(usage.model_dump(mode="json", by_alias=False, exclude_none=True))
        return CodexTurnResult(
            thread_id=thread.id,
            turn_id=str(result.id),
            status=str(status),
            final_response=str(result.final_response or ""),
            items=[self._serialize_item(item) for item in result.items],
            duration_ms=getattr(result, "duration_ms", None),
            usage=dict(usage_payload),
            account_type=str(capability["account_type"]),
            account_plan=(
                str(capability["account_plan"]) if capability.get("account_plan") else None
            ),
            runtime_version=str(capability["runtime_version"]),
        )


class CodexDirector:
    name = "codex-app-server"

    def __init__(
        self,
        *,
        client: CodexAppServerClient | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self.client = client or CodexAppServerClient(model=model, reasoning_effort=reasoning_effort)
        self.model = self.client.model
        self.reasoning_effort = self.client.reasoning_effort
        self.cwd = Path(cwd or os.getenv("STORYCANVAS_RUNS_DIR") or "./runs")

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
        prompt = (
            "Create the typed StoryCanvas DirectorDraft for this input. Return JSON only. "
            "For the Moon Garden three-shot demo, create exactly one shared style asset named "
            "style-bible and no other shared assets. Shot 1 must reference the style-bible; "
            "Shot 2 must reference the style-bible and previous Shot 1; Shot 3 must reference "
            "the style-bible and previous Shot 2. Preserve the red notebook, clay pot, botanist "
            "identity, moonlit glasshouse, blue vine, and exactly three glowing moths.\n\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        try:
            from openai.lib._pydantic import to_strict_json_schema
        except ImportError as error:  # pragma: no cover - pinned OpenAI SDK supplies this
            raise ProviderError(
                "The installed OpenAI SDK cannot build a strict JSON schema"
            ) from error
        result = self.client.run(
            prompt=prompt,
            cwd=self.cwd,
            output_schema=to_strict_json_schema(DirectorDraft),
            base_instructions=DIRECTOR_SYSTEM_PROMPT,
            workspace_write=False,
        )
        try:
            draft = DirectorDraft.model_validate_json(result.final_response)
        except Exception as error:
            raise ProviderError(
                f"Codex Director returned invalid structured JSON: {error}"
            ) from error
        if len(draft.shared_assets) != 1 or draft.shared_assets[0].asset_id != "style-bible":
            raise ProviderError("Moon Garden Codex plan must contain exactly one style-bible asset")
        descriptor = ProviderDescriptor(
            name=self.name,
            model=self.model,
            revision=result.runtime_version,
            endpoint_kind=(
                f"chatgpt-login;ephemeral;reasoning={self.reasoning_effort};"
                f"thread={result.thread_id};turn={result.turn_id}"
            ),
        )
        image_descriptor = ProviderDescriptor(
            name=self.name,
            model=self.model,
            revision=result.runtime_version,
            endpoint_kind=(
                f"imageGeneration+localImage;chatgpt-login;reasoning={self.reasoning_effort}"
            ),
        )
        plan = draft_to_plan(
            value,
            policy,
            draft,
            descriptor,
            fact_search_provider=ProviderDescriptor(
                name=self.name,
                model=self.model,
                revision=result.runtime_version,
                endpoint_kind="webSearch;chatgpt-login",
            ),
            image_provider=image_descriptor,
        )
        for shot in plan.shots:
            shot.references.sort(
                key=lambda item: 0 if item.provenance == Provenance.IMAGE_GENERATION else 1
            )
            for index, reference in enumerate(shot.references, start=1):
                reference.order = index
        return plan


class CodexImageProvider:
    name = "codex-app-server"

    def __init__(
        self,
        *,
        client: CodexAppServerClient | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.client = client or CodexAppServerClient(model=model, reasoning_effort=reasoning_effort)
        self.model = self.client.model
        self.reasoning_effort = self.client.reasoning_effort

    @staticmethod
    def _decode_result(value: str) -> bytes:
        encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ProviderError("Codex imageGeneration result is not valid base64") from error

    @staticmethod
    def _render_image(raw: bytes, destination: Path) -> None:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                rendered = source.convert("RGB")
                rendered.thumbnail((1344, 768), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (1344, 768), (0, 0, 0))
                canvas.paste(
                    rendered,
                    ((canvas.width - rendered.width) // 2, (canvas.height - rendered.height) // 2),
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                canvas.save(destination, format="PNG")
        except OSError as error:
            raise ProviderError("Codex imageGeneration output is not a decodable image") from error

    def generate(self, prompt: str, references: list[Path], destination: Path) -> GeneratedFile:
        missing = [str(path) for path in references if not path.is_file()]
        if missing:
            raise ProviderError(f"Missing image references: {missing}")
        request_identity = {
            "provider": self.name,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "prompt": prompt,
            "references": [str(path.resolve()) for path in references],
        }
        instruction = (
            "Use the native image generation tool exactly once to create a polished cinematic "
            "16:9 image. Do not use shell commands, web search, or any other tool. Treat every "
            "attached localImage as an ordered visual reference in the order provided. Preserve "
            "identity, wardrobe, location, prop state, composition intent, and visual style from "
            "the references. Do not add captions, logos, borders, or watermarks. Save the generated "
            f"image inside the current working directory. Generation prompt:\n{prompt}"
        )
        result = self.client.run(
            prompt=instruction,
            cwd=destination.parent,
            references=references,
            base_instructions=(
                "You are the StoryCanvas image renderer. Use native imageGeneration only, follow "
                "the supplied prompt and ordered localImage references exactly, and return no "
                "substitute or mock artifact."
            ),
            workspace_write=True,
        )
        image_items = [item for item in result.items if item.get("type") == "imageGeneration"]
        completed = [item for item in image_items if item.get("status") == "completed"]
        if not completed:
            statuses = [str(item.get("status")) for item in image_items]
            raise ProviderError(
                f"Codex returned no completed imageGeneration item; statuses={statuses or ['missing']}"
            )
        item = completed[-1]
        saved_path = item.get("saved_path") or item.get("savedPath")
        result_value = item.get("result")
        if saved_path:
            source = Path(str(saved_path)).expanduser()
            if not source.is_absolute():
                source = destination.parent / source
            if not source.is_file():
                raise ProviderError(f"Codex imageGeneration savedPath is missing: {source}")
            raw = source.read_bytes()
        elif isinstance(result_value, str) and result_value:
            raw = self._decode_result(result_value)
        else:
            raise ProviderError("Codex imageGeneration item has neither savedPath nor image data")
        self._render_image(raw, destination)
        actual_prompt = str(item.get("revised_prompt") or item.get("revisedPrompt") or prompt)
        item_id = str(item.get("id") or "") or None
        return GeneratedFile(
            path=destination,
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="image_generation",
                request_sha256=sha256_json(request_identity),
                provider_request_id=item_id,
                metadata={
                    "auth_type": result.account_type,
                    "account_plan": result.account_plan,
                    "reasoning_effort": self.reasoning_effort,
                    "runtime_version": result.runtime_version,
                    "thread_id": result.thread_id,
                    "turn_id": result.turn_id,
                    "item_id": item_id,
                    "item_status": item.get("status"),
                    "revised_prompt": actual_prompt,
                    "duration_ms": result.duration_ms,
                    "usage": result.usage,
                },
            ),
            metadata={
                "width": 1344,
                "height": 768,
                "reference_count": len(references),
                "actual_prompt": actual_prompt,
                "generation_mode": "native_imageGeneration",
                "auth_type": result.account_type,
                "reasoning_effort": self.reasoning_effort,
            },
        )
