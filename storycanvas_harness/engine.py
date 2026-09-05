from __future__ import annotations

import io
import ipaddress
import json
import os
import shutil
import socket
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urljoin, urlparse

import httpx
from filelock import FileLock
from PIL import Image

from .audit import render_audit
from .config import RuntimeSettings
from .errors import PolicyViolation, ProviderError, ResumeConflict
from .identity import plan_content, plan_sha256, provider_identity
from .media import concat_videos, full_decode, probe_media
from .protocol import (
    CANVAS_RENDER,
    EVALUATION_RUN,
    FACT_SEARCH,
    IMAGE_GENERATE,
    IMAGE_PROMPT_COMPILE,
    REFERENCE_PLAN,
    STORY_PLAN,
    VIDEO_GENERATE,
    VIDEO_PROMPT_COMPILE,
    VISUAL_SEARCH,
)
from .providers.base import (
    DirectorProvider,
    EvaluationProvider,
    FactSearchProvider,
    ImageProvider,
    PlanProcessor,
    SearchResult,
    VideoProvider,
    VisualSearchProvider,
    WorkflowRenderer,
)
from .providers.codex import CodexAppServerClient, CodexDirector, CodexImageProvider
from .providers.director import DeterministicDirector, OpenAIDirector
from .providers.image import MockImageProvider, OpenAIImageProvider
from .providers.search import MockFactSearch, MockVisualSearch, OpenAIFactSearch, SerperVisualSearch
from .providers.video import MiniMaxH3CompatibleProvider, MockVideoProvider
from .schemas import (
    ArtifactRecord,
    AttemptRecord,
    CanvasPlan,
    CompiledWorkflow,
    ExecutionMode,
    ExecutionPolicy,
    InputMode,
    PlannedAsset,
    PlannedReference,
    Provenance,
    ProviderReceipt,
    RunManifest,
    RunRecord,
    RunStatus,
    ShotInput,
    StoryInput,
)
from .storage import append_jsonl, artifact_path, read_jsonl
from .utils import (
    atomic_write_json,
    atomic_write_text,
    safe_error,
    sha256_file,
    sha256_json,
    utc_now,
)
from .workflow import compile_workflow

if TYPE_CHECKING:
    from .plugins.registry import PluginRegistry


class StoryCanvas:
    """Python SDK and deterministic execution harness.

    The agent returns a typed CanvasPlan. This class validates the plan, enforces
    explicit spend limits, and executes only the deterministic dependency graph.
    """

    def __init__(
        self,
        *,
        runs_dir: str | Path | None = None,
        director: DirectorProvider | None = None,
        fact_search: FactSearchProvider | None = None,
        visual_search: VisualSearchProvider | None = None,
        image_provider: ImageProvider | None = None,
        video_provider: VideoProvider | None = None,
        workflow_renderer: WorkflowRenderer | None = None,
        plan_processors: list[PlanProcessor] | None = None,
        evaluator: EvaluationProvider | None = None,
    ):
        self.runs_dir = (
            Path(runs_dir or os.environ.get("STORYCANVAS_RUNS_DIR", "./runs"))
            .expanduser()
            .resolve()
        )
        self.director = director or DeterministicDirector()
        self.fact_search = fact_search
        self.visual_search = visual_search
        self.image_provider = image_provider
        self.video_provider = video_provider
        self.workflow_renderer = workflow_renderer
        self.plan_processors = plan_processors or []
        self.evaluator = evaluator
        self._plugin_registry: PluginRegistry | None = None
        self._owned_clients: list[Any] = []

    @classmethod
    def from_registry(
        cls,
        registry: PluginRegistry,
        *,
        runs_dir: str | Path | None = None,
    ) -> StoryCanvas:
        """Compose the current engine from Plugin API v1 capability services."""

        canvas = cls(
            runs_dir=runs_dir,
            director=cast(DirectorProvider, registry.resolve_service(STORY_PLAN)),
            fact_search=(
                cast(FactSearchProvider, registry.resolve_service(FACT_SEARCH))
                if registry.has_capability(FACT_SEARCH, active_only=True)
                else None
            ),
            visual_search=(
                cast(VisualSearchProvider, registry.resolve_service(VISUAL_SEARCH))
                if registry.has_capability(VISUAL_SEARCH, active_only=True)
                else None
            ),
            image_provider=(
                cast(ImageProvider, registry.resolve_service(IMAGE_GENERATE))
                if registry.has_capability(IMAGE_GENERATE, active_only=True)
                else None
            ),
            video_provider=(
                cast(VideoProvider, registry.resolve_service(VIDEO_GENERATE))
                if registry.has_capability(VIDEO_GENERATE, active_only=True)
                else None
            ),
            workflow_renderer=(
                cast(WorkflowRenderer, registry.resolve_service(CANVAS_RENDER))
                if registry.has_capability(CANVAS_RENDER, active_only=True)
                else None
            ),
            plan_processors=[
                cast(PlanProcessor, registry.resolve_service(capability))
                for capability in (
                    REFERENCE_PLAN,
                    IMAGE_PROMPT_COMPILE,
                    VIDEO_PROMPT_COMPILE,
                )
                if registry.has_capability(capability, active_only=True)
            ],
            evaluator=(
                cast(EvaluationProvider, registry.resolve_service(EVALUATION_RUN))
                if registry.has_capability(EVALUATION_RUN, active_only=True)
                else None
            ),
        )
        canvas._plugin_registry = registry
        return canvas

    @classmethod
    def from_profile(
        cls,
        profile_path: str | Path,
        *,
        runs_dir: str | Path | None = None,
    ) -> StoryCanvas:
        from .plugins import build_builtin_registry, load_profile

        selected_runs_dir = (
            Path(runs_dir or os.environ.get("STORYCANVAS_RUNS_DIR", "./runs"))
            .expanduser()
            .resolve()
        )
        profile = load_profile(Path(profile_path))
        registry = build_builtin_registry(profile, root=selected_runs_dir.parent)
        try:
            return cls.from_registry(registry, runs_dir=selected_runs_dir)
        except Exception:
            registry.dispose_all()
            raise

    def close(self) -> None:
        try:
            if self._plugin_registry is not None:
                self._plugin_registry.dispose_all()
        finally:
            self._plugin_registry = None
            failures = []
            for client in self._owned_clients:
                try:
                    client.close()
                except Exception as error:
                    failures.append(safe_error(error))
            self._owned_clients.clear()
            if failures:
                raise ProviderError("Provider cleanup failed: " + "; ".join(failures))

    def __enter__(self) -> StoryCanvas:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @classmethod
    def from_environment(
        cls, *, runs_dir: str | Path | None = None, videos_only: bool = False
    ) -> StoryCanvas:
        settings = RuntimeSettings.from_environment(runs_dir=runs_dir)
        runs_dir = settings.runs_dir
        mode = settings.provider_mode
        if mode == "mock":
            return cls(
                runs_dir=runs_dir,
                director=DeterministicDirector(),
                fact_search=MockFactSearch(),
                visual_search=MockVisualSearch(),
                image_provider=MockImageProvider(),
                video_provider=MockVideoProvider(),
            )
        if videos_only:
            return cls(
                runs_dir=runs_dir,
                video_provider=MiniMaxH3CompatibleProvider()
                if os.getenv("MINIMAX_H3_API_KEY") and os.getenv("MINIMAX_H3_BASE_URL")
                else None,
            )
        if mode == "codex":
            if not settings.codex_enabled:
                raise ProviderError("Codex provider mode requires STORYCANVAS_CODEX_ENABLED=true")
            selected_runs_dir = (
                Path(runs_dir or os.environ.get("STORYCANVAS_RUNS_DIR", "./runs"))
                .expanduser()
                .resolve()
            )
            codex_client = CodexAppServerClient()
            video_provider = None
            if os.getenv("MINIMAX_H3_API_KEY") and os.getenv("MINIMAX_H3_BASE_URL"):
                video_provider = MiniMaxH3CompatibleProvider()
            return cls(
                runs_dir=selected_runs_dir,
                director=CodexDirector(client=codex_client, cwd=selected_runs_dir),
                fact_search=None,
                visual_search=None,
                image_provider=CodexImageProvider(client=codex_client),
                video_provider=video_provider,
            )
        api_key = os.getenv("OPENAI_API_KEY", "")
        director: DirectorProvider
        fact_search: FactSearchProvider | None
        visual_search: VisualSearchProvider | None
        image_provider: ImageProvider | None
        if api_key:
            director = OpenAIDirector()
            fact_search = OpenAIFactSearch()
            image_provider = OpenAIImageProvider()
        else:
            director = DeterministicDirector()
            fact_search = None
            image_provider = None
        visual_search = SerperVisualSearch() if os.getenv("SERPER_API_KEY") else None
        video_provider = None
        if os.getenv("MINIMAX_H3_API_KEY") and os.getenv("MINIMAX_H3_BASE_URL"):
            video_provider = MiniMaxH3CompatibleProvider()
        canvas = cls(
            runs_dir=runs_dir,
            director=director,
            fact_search=fact_search,
            visual_search=visual_search,
            image_provider=image_provider,
            video_provider=video_provider,
        )
        if api_key:
            canvas._owned_clients = list(
                {
                    id(client): client
                    for provider in (director, fact_search, image_provider)
                    if (client := getattr(provider, "client", None)) is not None
                }.values()
            )
        return canvas

    def plan(
        self, value: ShotInput | StoryInput, policy: ExecutionPolicy | None = None
    ) -> CanvasPlan:
        selected_policy = policy or ExecutionPolicy()
        plan = self.director.plan(value, selected_policy)
        for processor in self.plan_processors:
            original_input_sha = plan.input_sha256
            try:
                transformed = processor.transform(
                    plan.model_copy(deep=True), value, selected_policy
                )
                plan = CanvasPlan.model_validate(
                    transformed.model_dump(mode="json", exclude_none=True)
                )
            except Exception as error:
                raise ProviderError(
                    f"Plan processor {processor.name!r} failed: {type(error).__name__}: {error}"
                ) from error
            if plan.input_sha256 != original_input_sha:
                raise ProviderError(
                    f"Plan processor {processor.name!r} changed immutable input identity"
                )
            plan.warnings.append(f"Plan processor applied: {processor.name} ({processor.model}).")
        if (
            isinstance(self.director, DeterministicDirector)
            and self._plugin_registry is None
            and os.getenv("STORYCANVAS_PROVIDER_MODE", "openai").casefold() != "mock"
        ):
            plan.warnings.append(
                "OPENAI_API_KEY is not configured; this is an offline preview plan, not a production Agent plan."
            )
        plan = CanvasPlan.model_validate(plan.model_dump(mode="json"))
        plan.call_estimate = plan.required_calls
        plan.plan_id = f"plan-{plan_sha256(plan)[:16]}"
        return plan

    def execution_sha256(self, plan: CanvasPlan, policy: ExecutionPolicy) -> str:
        return sha256_json(
            {
                "identity_version": 2,
                "plan": plan_content(plan),
                "policy": policy,
                "composition_sha256": self._plugin_registry.composition_sha256
                if self._plugin_registry
                else None,
                "providers": {
                    key: provider_identity(getattr(self, key))
                    for key in (
                        "director",
                        "fact_search",
                        "visual_search",
                        "image_provider",
                        "video_provider",
                        "workflow_renderer",
                        "evaluator",
                    )
                },
            }
        )

    def compile_workflow(
        self, plan: CanvasPlan, policy: ExecutionPolicy | None = None
    ) -> CompiledWorkflow:
        plan = CanvasPlan.model_validate(plan.model_dump(mode="json"))
        selected_policy = policy or ExecutionPolicy()
        if self.workflow_renderer is not None:
            return self.workflow_renderer.compile(plan, selected_policy)
        return compile_workflow(plan, selected_policy)

    def _preflight(
        self, plan: CanvasPlan, policy: ExecutionPolicy, *, videos_only: bool = False
    ) -> None:
        plan = CanvasPlan.model_validate(plan.model_dump(mode="json"))
        policy = ExecutionPolicy.model_validate(policy.model_dump(mode="json"))
        estimate = plan.required_calls
        if len(plan.shots) > policy.max_shots:
            raise PolicyViolation(
                f"Plan contains {len(plan.shots)} shots; max_shots is {policy.max_shots}"
            )
        if not videos_only and policy.mode in {ExecutionMode.ASSETS, ExecutionMode.FULL}:
            if estimate.image_generation_calls > policy.max_image_calls:
                raise PolicyViolation(
                    f"Plan needs {estimate.image_generation_calls} image calls; "
                    f"policy allows {policy.max_image_calls}"
                )
            total_search_calls = estimate.fact_search_calls + estimate.visual_search_calls
            if total_search_calls > policy.max_search_calls:
                raise PolicyViolation(
                    f"Plan needs {total_search_calls} search calls; "
                    f"policy allows {policy.max_search_calls}"
                )
            if estimate.fact_search_calls and self.fact_search is None:
                raise ProviderError(
                    "The plan requires factual search but no provider is configured"
                )
            if estimate.visual_search_calls and self.visual_search is None:
                raise ProviderError("The plan requires visual search but no provider is configured")
            if self.image_provider is None:
                raise ProviderError(
                    "No image provider is configured. Use Codex login mode, set OPENAI_API_KEY, "
                    "or explicitly use mock mode."
                )
        if policy.mode == ExecutionMode.FULL:
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                raise ProviderError(
                    "Video execution requires ffmpeg and ffprobe; run storycanvas doctor --mode full"
                )
            if not policy.allow_paid_video:
                raise PolicyViolation("Full execution requires allow_paid_video=true")
            if estimate.video_generation_calls > policy.max_video_calls:
                raise PolicyViolation(
                    f"Plan needs {estimate.video_generation_calls} video calls; "
                    f"policy allows {policy.max_video_calls}"
                )
            if self.video_provider is None:
                raise ProviderError("No video provider is configured")

    @staticmethod
    def _write_prompt_rows(root: Path, rows: list[dict[str, Any]]) -> None:
        text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
        atomic_write_text(root / "prompts.jsonl", text)

    @staticmethod
    def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
        append_jsonl(path, row)

    @staticmethod
    def _reference_paths(
        references: list[PlannedReference], generated: dict[str, ArtifactRecord]
    ) -> tuple[list[Path], list[PlannedReference]]:
        paths: list[Path] = []
        resolved: list[PlannedReference] = []
        for reference in references:
            item = reference.model_copy(deep=True)
            if reference.source_asset_id:
                source = generated.get(reference.source_asset_id)
                if source is None:
                    raise ProviderError(f"Dependency is not ready: {reference.source_asset_id}")
                path = Path(source.path)
                if not path.is_file() or sha256_file(path) != source.sha256:
                    raise ProviderError(
                        f"Generated reference SHA mismatch: {reference.source_asset_id}"
                    )
                item.path = source.path
                item.sha256 = source.sha256
            elif reference.path:
                path = Path(reference.path).expanduser()
                if not path.is_file():
                    raise ProviderError(f"Reference image does not exist: {path}")
                actual_sha = sha256_file(path)
                if reference.sha256 and reference.sha256 != actual_sha:
                    raise ProviderError(f"Reference SHA mismatch: {path}")
                item.sha256 = actual_sha
            else:
                raise ProviderError(
                    f"Reference has no path or source asset: {reference.reference_id}"
                )
            paths.append(path)
            resolved.append(item)
        return paths, resolved

    @staticmethod
    def _require_public_https(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ProviderError("Visual search images must use a public HTTPS URL")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
            addresses = [literal]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(row[4][0])
                    for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
                ]
            except OSError as error:
                raise ProviderError(
                    f"Could not resolve visual search host: {parsed.hostname}"
                ) from error
        if not addresses or any(not address.is_global for address in addresses):
            raise ProviderError("Visual search URL resolves to a non-public address")

    @classmethod
    def _download_public_image(cls, url: str, destination: Path) -> None:
        current = url
        buffer = io.BytesIO()
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            for _ in range(5):
                cls._require_public_https(current)
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ProviderError("Visual search redirect has no location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").casefold()
                    if not content_type.startswith("image/"):
                        raise ProviderError(
                            f"Visual search URL returned non-image content: {content_type or 'unknown'}"
                        )
                    declared = int(response.headers.get("content-length", "0") or 0)
                    if declared > 20 * 1024 * 1024:
                        raise ProviderError("Visual search image exceeds the 20 MiB limit")
                    for chunk in response.iter_bytes(1024 * 1024):
                        if buffer.tell() + len(chunk) > 20 * 1024 * 1024:
                            raise ProviderError("Visual search image exceeds the 20 MiB limit")
                        buffer.write(chunk)
                    break
            else:
                raise ProviderError("Visual search image exceeded the redirect limit")
        if not buffer.tell():
            raise ProviderError("Visual search image is empty")
        buffer.seek(0)
        try:
            with Image.open(buffer) as source:
                source.load()
                rendered = source.convert("RGB")
                rendered.thumbnail((1344, 768), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (1344, 768), (0, 0, 0))
                canvas.paste(
                    rendered,
                    ((canvas.width - rendered.width) // 2, (canvas.height - rendered.height) // 2),
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                canvas.save(destination, format="PNG")
        except (OSError, ValueError) as error:
            raise ProviderError("Visual search result is not a decodable image") from error

    def _visual_source_artifact(
        self,
        *,
        root: Path,
        asset_id: str,
        query: str,
        result: SearchResult,
    ) -> ArtifactRecord:
        hit = next((item for item in result.hits if item.image_url), None)
        if hit is None or hit.image_url is None:
            raise ProviderError(f"Visual search returned no image for {asset_id}")
        source_id = f"visual-{sha256_json({'asset_id': asset_id, 'query': query})[:20]}"
        destination = root / "assets" / "search" / f"{source_id}.png"
        receipt_path = root / "receipts" / f"{source_id}.json"
        request_sha = sha256_json(
            {
                "provider": result.receipt.provider,
                "query": query,
                "page_url": hit.url,
                "image_url": hit.image_url,
            }
        )
        if destination.is_file() and receipt_path.is_file():
            persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            if persisted.get("request_sha256") == request_sha and persisted.get("artifact", {}).get(
                "sha256"
            ) == sha256_file(destination):
                artifact = ArtifactRecord.model_validate(persisted["artifact"])
                artifact.metadata["cache"] = "hit"
                return artifact
        self._download_public_image(hit.image_url, destination)
        artifact = ArtifactRecord(
            artifact_id=source_id,
            kind="image",
            path=str(destination),
            sha256=sha256_file(destination),
            provenance=Provenance.VISUAL_SEARCH,
            prompt=query,
            prompt_sha256=sha256_json({"query": query}),
            input_mode=InputMode.TEXT,
            receipt=ProviderReceipt(
                provider=result.receipt.provider,
                model=result.receipt.model,
                operation="visual_search_download",
                request_sha256=request_sha,
                provider_request_id=result.receipt.provider_request_id,
                metadata={"search_receipt": result.receipt.model_dump(mode="json")},
            ),
            metadata={
                "cache": "miss",
                "query": query,
                "page_url": hit.url,
                "image_url": hit.image_url,
                "publisher": hit.publisher,
            },
        )
        atomic_write_json(
            receipt_path,
            {"request_sha256": request_sha, "artifact": artifact},
        )
        return artifact

    @staticmethod
    def _cached_search(
        *,
        root: Path,
        asset_id: str,
        query: str,
        provider: FactSearchProvider | VisualSearchProvider | None,
        kind: str,
    ) -> tuple[SearchResult, bool]:
        receipt_path = root / "receipts" / f"{asset_id}.{kind}.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(receipt_path) + ".lock"):
            return StoryCanvas._cached_search_locked(
                root=root, asset_id=asset_id, query=query, provider=provider, kind=kind
            )

    @staticmethod
    def _cached_search_locked(
        *,
        root: Path,
        asset_id: str,
        query: str,
        provider: FactSearchProvider | VisualSearchProvider | None,
        kind: str,
    ) -> tuple[SearchResult, bool]:
        """Run a search once and persist a provider-independent recovery receipt."""
        if provider is None:
            raise ProviderError(f"Asset {asset_id} requires {kind} but no provider is configured")
        receipt_path = root / "receipts" / f"{asset_id}.{kind}.json"
        query_sha = sha256_json(
            {"provider": provider_identity(provider), "query": query, "kind": kind}
        )
        if receipt_path.is_file():
            persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            if persisted.get("query_sha256") == query_sha and persisted.get("result"):
                return SearchResult.model_validate(persisted["result"]), True
        result = provider.search(query)
        atomic_write_json(receipt_path, {"query_sha256": query_sha, "result": result})
        return result, False

    def _generate_image(
        self,
        *,
        root: Path,
        artifact_id: str,
        prompt: str,
        planned_prompt: str | None,
        references: list[PlannedReference],
        generated: dict[str, ArtifactRecord],
        metadata: dict[str, Any],
    ) -> tuple[ArtifactRecord, dict[str, Any]]:
        lock_path = root / "receipts" / f"{artifact_id}.image.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path)):
            return self._generate_image_locked(
                root=root,
                artifact_id=artifact_id,
                prompt=prompt,
                planned_prompt=planned_prompt,
                references=references,
                generated=generated,
                metadata=metadata,
            )

    def _generate_image_locked(
        self,
        *,
        root: Path,
        artifact_id: str,
        prompt: str,
        planned_prompt: str | None,
        references: list[PlannedReference],
        generated: dict[str, ArtifactRecord],
        metadata: dict[str, Any],
    ) -> tuple[ArtifactRecord, dict[str, Any]]:
        if self.image_provider is None:
            raise ProviderError("No image provider is configured")
        reference_paths, resolved = self._reference_paths(references, generated)
        output = root / "assets" / f"{artifact_id}.png"
        receipt_path = root / "receipts" / f"{artifact_id}.json"
        attempt_journal = root / "receipts" / f"{artifact_id}.attempts.jsonl"
        identity = {
            "provider": provider_identity(self.image_provider),
            "model": self.image_provider.model,
            "prompt": prompt,
            "references": [reference.sha256 for reference in resolved],
            "output": output.name,
        }
        cache_request_sha = sha256_json(identity)
        if output.is_file() and receipt_path.is_file():
            persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            if persisted.get("cache_request_sha256") == cache_request_sha and persisted.get(
                "artifact", {}
            ).get("sha256") == sha256_file(output):
                artifact = ArtifactRecord.model_validate(persisted["artifact"])
                artifact.path = str(output)
                artifact.ordered_references = resolved
                artifact.metadata["cache"] = "hit"
                return artifact, {
                    "asset_id": artifact_id,
                    "planned_prompt": planned_prompt or prompt,
                    "actual_prompt": artifact.prompt or prompt,
                    "prompt_sha256": artifact.prompt_sha256 or sha256_json({"prompt": prompt}),
                    "receipt": artifact.receipt.model_dump(mode="json", exclude_none=True)
                    if artifact.receipt
                    else None,
                    "input_mode": artifact.input_mode.value if artifact.input_mode else "text",
                    "ordered_references": [item.model_dump(mode="json") for item in resolved],
                    "output_sha256": artifact.sha256,
                    "cache": "hit",
                }
        previous_attempts: list[AttemptRecord] = []
        for row in read_jsonl(attempt_journal):
            previous_attempts.append(AttemptRecord.model_validate(row["attempt"]))
        started = utc_now()
        attempt = AttemptRecord(
            attempt=len(previous_attempts) + 1,
            started_at=started,
            status="running",
        )
        input_mode = InputMode.TEXT_IMAGE if reference_paths else InputMode.TEXT
        try:
            generated_file = self.image_provider.generate(prompt, reference_paths, output)
            with Image.open(output) as image:
                image.verify()
            attempt.status = "success"
            attempt.finished_at = utc_now()
        except Exception as error:
            attempt.status = "failed"
            attempt.finished_at = utc_now()
            attempt.error_type = type(error).__name__
            attempt.error_message = safe_error(error)
            self._append_jsonl(
                attempt_journal,
                {
                    "artifact_id": artifact_id,
                    "attempt": attempt.model_dump(mode="json"),
                    "planned_prompt": planned_prompt or prompt,
                    "actual_prompt": prompt,
                    "prompt_sha256": sha256_json({"prompt": prompt}),
                    "input_mode": input_mode.value,
                    "ordered_references": [item.model_dump(mode="json") for item in resolved],
                },
            )
            raise
        actual_provider_prompt = str(generated_file.metadata.get("actual_prompt") or prompt)
        self._append_jsonl(
            attempt_journal,
            {
                "artifact_id": artifact_id,
                "attempt": attempt.model_dump(mode="json"),
                "planned_prompt": planned_prompt or prompt,
                "actual_prompt": actual_provider_prompt,
                "prompt_sha256": sha256_json({"prompt": actual_provider_prompt}),
                "input_mode": input_mode.value,
                "ordered_references": [item.model_dump(mode="json") for item in resolved],
                "receipt": generated_file.receipt.model_dump(mode="json", exclude_none=True),
                "output_sha256": sha256_file(output),
            },
        )
        artifact = ArtifactRecord(
            artifact_id=artifact_id,
            kind="image",
            path=str(output),
            sha256=sha256_file(output),
            provenance=Provenance.IMAGE_GENERATION,
            prompt=actual_provider_prompt,
            prompt_sha256=sha256_json({"prompt": actual_provider_prompt}),
            input_mode=input_mode,
            ordered_references=resolved,
            receipt=generated_file.receipt,
            attempts=[*previous_attempts, attempt],
            metadata={**metadata, **generated_file.metadata, "cache": "miss"},
        )
        atomic_write_json(
            receipt_path,
            {"cache_request_sha256": cache_request_sha, "artifact": artifact},
        )
        return artifact, {
            "asset_id": artifact_id,
            "planned_prompt": planned_prompt or prompt,
            "actual_prompt": actual_provider_prompt,
            "prompt_sha256": artifact.prompt_sha256,
            "input_mode": input_mode.value,
            "ordered_references": [item.model_dump(mode="json") for item in resolved],
            "receipt": generated_file.receipt.model_dump(mode="json", exclude_none=True),
            "output_sha256": artifact.sha256,
            "cache": "miss",
        }

    def _execute_assets(
        self,
        plan: CanvasPlan,
        policy: ExecutionPolicy,
        root: Path,
        manifest: RunManifest,
    ) -> tuple[dict[str, ArtifactRecord], list[dict[str, Any]]]:
        generated: dict[str, ArtifactRecord] = {}
        prompt_rows: list[dict[str, Any]] = []
        shared_lookup = {asset.asset_id: asset for asset in plan.shared_assets}
        shot_lookup = {shot.keyframe_asset_id: shot for shot in plan.shots}
        pending = set(shared_lookup) | set(shot_lookup)
        attempts_before = {
            key: len(read_jsonl(root / "receipts" / f"{key}.attempts.jsonl")) for key in pending
        }
        dependencies: dict[str, set[str]] = {
            asset.asset_id: set(asset.dependencies) for asset in plan.shared_assets
        }
        dependencies.update({shot.keyframe_asset_id: set(shot.dependencies) for shot in plan.shots})
        failures: set[str] = set()
        search_count_lock = Lock()
        search_calls = 0
        search_cache_hits = 0
        visual_search_calls = 0
        visual_search_cache_hits = 0

        def execute(
            artifact_id: str,
        ) -> tuple[ArtifactRecord, dict[str, Any], list[ArtifactRecord]]:
            nonlocal search_calls, search_cache_hits
            nonlocal visual_search_calls, visual_search_cache_hits
            auxiliary: list[ArtifactRecord] = []
            if artifact_id in shared_lookup:
                asset: PlannedAsset = shared_lookup[artifact_id]
                actual_prompt = asset.actual_prompt
                references = list(asset.references)
                if asset.search_query:
                    search, cache_hit = self._cached_search(
                        root=root,
                        asset_id=artifact_id,
                        query=asset.search_query,
                        provider=self.fact_search,
                        kind="fact-search",
                    )
                    with search_count_lock:
                        if cache_hit:
                            search_cache_hits += 1
                        else:
                            search_calls += 1
                    actual_prompt = f"{actual_prompt}\n\nVerified visual facts from cited search results:\n{search.summary}"
                if asset.visual_search_query:
                    visual, cache_hit = self._cached_search(
                        root=root,
                        asset_id=artifact_id,
                        query=asset.visual_search_query,
                        provider=self.visual_search,
                        kind="visual-search",
                    )
                    with search_count_lock:
                        if cache_hit:
                            visual_search_cache_hits += 1
                        else:
                            visual_search_calls += 1
                    source = self._visual_source_artifact(
                        root=root,
                        asset_id=artifact_id,
                        query=asset.visual_search_query,
                        result=visual,
                    )
                    if len(references) >= 5:
                        raise PolicyViolation(
                            f"Asset {artifact_id} has no free reference slot for visual search"
                        )
                    auxiliary.append(source)
                    references.append(
                        PlannedReference(
                            order=len(references) + 1,
                            reference_id=source.artifact_id,
                            role="visual_search_exemplar",
                            provenance=Provenance.VISUAL_SEARCH,
                            path=source.path,
                            sha256=source.sha256,
                            description=f"Selected from {source.metadata.get('page_url')}",
                        )
                    )
                artifact, row = self._generate_image(
                    root=root,
                    artifact_id=artifact_id,
                    prompt=actual_prompt,
                    planned_prompt=asset.planned_prompt,
                    references=references,
                    generated=generated,
                    metadata={"asset_kind": asset.kind, "role": asset.role},
                )
                return artifact, row, auxiliary
            shot = shot_lookup[artifact_id]
            artifact, row = self._generate_image(
                root=root,
                artifact_id=artifact_id,
                prompt=shot.image_prompt,
                planned_prompt=shot.image_prompt,
                references=shot.references,
                generated=generated,
                metadata={"asset_kind": "shot_keyframe", "shot_id": shot.shot_id},
            )
            return artifact, row, auxiliary

        running: dict[Future[tuple[ArtifactRecord, dict[str, Any], list[ArtifactRecord]]], str] = {}
        with ThreadPoolExecutor(max_workers=policy.max_concurrency) as executor:
            while pending or running:
                blocked_by_failure = {item for item in pending if dependencies[item] & failures}
                for artifact_id in sorted(blocked_by_failure):
                    pending.remove(artifact_id)
                    failures.add(artifact_id)
                    manifest.errors.append(
                        {
                            "artifact_id": artifact_id,
                            "error_type": "BlockedDependency",
                            "error": f"Blocked by {sorted(dependencies[artifact_id] & failures)}",
                        }
                    )
                ready = [
                    item
                    for item in sorted(pending)
                    if dependencies[item] <= set(generated)
                    and len(running) < policy.max_concurrency
                ]
                for artifact_id in ready[: max(0, policy.max_concurrency - len(running))]:
                    pending.remove(artifact_id)
                    running[executor.submit(execute, artifact_id)] = artifact_id
                if not running:
                    if pending:
                        for artifact_id in sorted(pending):
                            manifest.errors.append(
                                {
                                    "artifact_id": artifact_id,
                                    "error_type": "UnresolvableDependency",
                                    "error": f"Dependencies: {sorted(dependencies[artifact_id])}",
                                }
                            )
                            failures.add(artifact_id)
                        pending.clear()
                    break
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    artifact_id = running.pop(future)
                    try:
                        artifact, prompt_row, auxiliary = future.result()
                        generated[artifact_id] = artifact
                        manifest.artifacts.extend(auxiliary)
                        manifest.artifacts.append(artifact)
                        prompt_rows.append(prompt_row)
                    except Exception as error:
                        failures.add(artifact_id)
                        manifest.errors.append(
                            {
                                "artifact_id": artifact_id,
                                "error_type": type(error).__name__,
                                "error": safe_error(error),
                            }
                        )
        manifest.call_counts["image_generation"] = sum(
            len(read_jsonl(root / "receipts" / f"{key}.attempts.jsonl")) - before
            for key, before in attempts_before.items()
        )
        manifest.call_counts["image_successes"] = sum(
            artifact.metadata.get("cache") != "hit" for artifact in generated.values()
        )
        manifest.call_counts["image_cache_hits"] = sum(
            artifact.metadata.get("cache") == "hit" for artifact in generated.values()
        )
        manifest.call_counts["fact_search"] = search_calls
        manifest.call_counts["fact_search_cache_hits"] = search_cache_hits
        manifest.call_counts["visual_search"] = visual_search_calls
        manifest.call_counts["visual_search_cache_hits"] = visual_search_cache_hits
        return generated, prompt_rows

    def _execute_videos(
        self,
        plan: CanvasPlan,
        root: Path,
        manifest: RunManifest,
        generated: dict[str, ArtifactRecord],
        prompt_rows: list[dict[str, Any]],
    ) -> None:
        if self.video_provider is None:
            raise ProviderError("No video provider is configured")
        videos: list[Path] = []
        manifest.call_counts["video_generation"] = 0
        manifest.call_counts["video_attempts"] = 0
        manifest.call_counts["video_cache_hits"] = 0
        for shot in plan.shots:
            keyframe = generated.get(shot.keyframe_asset_id)
            if keyframe is None:
                manifest.errors.append(
                    {
                        "shot_id": shot.shot_id,
                        "error_type": "MissingKeyframe",
                        "error": "Video skipped because the final Canvas keyframe is unavailable",
                    }
                )
                continue
            other_paths, resolved = self._reference_paths(shot.references, generated)
            references = [Path(keyframe.path), *other_paths]
            ordered = [
                PlannedReference(
                    order=1,
                    reference_id=shot.keyframe_asset_id,
                    role="final_canvas",
                    provenance=Provenance.GENERATED_CANVAS,
                    source_asset_id=shot.keyframe_asset_id,
                    path=keyframe.path,
                    sha256=keyframe.sha256,
                ),
                *[
                    reference.model_copy(update={"order": index})
                    for index, reference in enumerate(resolved, start=2)
                ],
            ]
            if len(references) > 9:
                raise PolicyViolation(f"{shot.shot_id} exceeds the H3 9-image limit")
            destination = root / "videos" / "shots" / f"{shot.shot_id}.mp4"
            state_path = root / "receipts" / f"{shot.shot_id}.video-task.json"
            previous_task = (
                json.loads(state_path.read_text()).get("task_id") if state_path.is_file() else None
            )
            counted_creation = False
            try:
                manifest.call_counts["video_attempts"] += 1
                generated_file = self.video_provider.generate(
                    shot.h3_prompt, references, destination, state_path
                )
                created = bool(
                    generated_file.metadata.get(
                        "task_created", not generated_file.metadata.get("resumed", False)
                    )
                )
                manifest.call_counts["video_generation"] += int(created)
                counted_creation = created
                manifest.call_counts["video_cache_hits"] += int(not created)
                media = probe_media(destination)
                full_decode(destination)
                artifact = ArtifactRecord(
                    artifact_id=f"{shot.shot_id}-video",
                    kind="video",
                    path=str(destination),
                    sha256=sha256_file(destination),
                    prompt=shot.h3_prompt,
                    prompt_sha256=sha256_json({"prompt": shot.h3_prompt}),
                    input_mode=InputMode.TEXT_IMAGE,
                    ordered_references=ordered,
                    receipt=generated_file.receipt,
                    metadata={**generated_file.metadata, "media": media, "shot_id": shot.shot_id},
                )
                manifest.artifacts.append(artifact)
                videos.append(destination)
                prompt_rows.append(
                    {
                        "asset_id": artifact.artifact_id,
                        "actual_prompt": shot.h3_prompt,
                        "prompt_sha256": artifact.prompt_sha256,
                        "input_mode": "text+image",
                        "ordered_references": [item.model_dump(mode="json") for item in ordered],
                        "receipt": generated_file.receipt.model_dump(
                            mode="json", exclude_none=True
                        ),
                        "output_sha256": artifact.sha256,
                    }
                )
            except Exception as error:
                if not counted_creation and not previous_task and state_path.is_file():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if state.get("task_id") or state.get("status") in {
                        "creating",
                        "create_ambiguous",
                    }:
                        manifest.call_counts["video_generation"] += 1
                manifest.errors.append(
                    {
                        "shot_id": shot.shot_id,
                        "error_type": type(error).__name__,
                        "error": safe_error(error),
                    }
                )
        manifest.call_counts["video_successes"] = len(videos)
        if len(videos) == len(plan.shots):
            story_path = root / "videos" / "story.mp4"
            concat_videos(videos, story_path)
            full_decode(story_path)
            manifest.artifacts.append(
                ArtifactRecord(
                    artifact_id="story-video",
                    kind="video",
                    path=str(story_path),
                    sha256=sha256_file(story_path),
                    metadata={"assembled_from": [path.name for path in videos]},
                )
            )

    def _execute_evaluation(
        self,
        plan: CanvasPlan,
        root: Path,
        manifest: RunManifest,
    ) -> None:
        if self.evaluator is None or manifest.policy.mode == ExecutionMode.PLAN_ONLY:
            return
        manifest.call_counts["evaluation"] = 1
        try:
            candidates = self.evaluator.evaluate(
                plan.model_copy(deep=True),
                manifest.model_copy(deep=True),
                root,
            )
            known_ids = {artifact.artifact_id for artifact in manifest.artifacts}
            resolved_root = root.resolve()
            for value in candidates:
                artifact = ArtifactRecord.model_validate(
                    value.model_dump(mode="json", exclude_none=True)
                )
                if artifact.artifact_id in known_ids:
                    raise ProviderError(
                        f"Evaluator returned duplicate artifact id: {artifact.artifact_id}"
                    )
                candidate_path = Path(artifact.path)
                resolved = (
                    candidate_path.resolve()
                    if candidate_path.is_absolute()
                    else (resolved_root / candidate_path).resolve()
                )
                try:
                    resolved.relative_to(resolved_root)
                except ValueError as error:
                    raise ProviderError(
                        f"Evaluator artifact falls outside the Run root: {artifact.path}"
                    ) from error
                if not resolved.is_file():
                    raise ProviderError(f"Evaluator artifact does not exist: {resolved}")
                if sha256_file(resolved) != artifact.sha256:
                    raise ProviderError(
                        f"Evaluator artifact SHA256 mismatch: {artifact.artifact_id}"
                    )
                if artifact.receipt is None or artifact.receipt.provider != self.evaluator.name:
                    raise ProviderError(
                        f"Evaluator artifact {artifact.artifact_id!r} requires a matching receipt"
                    )
                artifact.path = str(resolved)
                manifest.artifacts.append(artifact)
                known_ids.add(artifact.artifact_id)
            manifest.call_counts["evaluation_artifacts"] = len(candidates)
        except Exception as error:
            manifest.errors.append(
                {
                    "stage": "evaluation",
                    "provider": self.evaluator.name,
                    "error_type": type(error).__name__,
                    "error": safe_error(error),
                }
            )
            manifest.status = RunStatus.PARTIAL

    def _finish_run(
        self, plan: CanvasPlan, manifest: RunManifest, root: Path, rows: list[dict[str, Any]]
    ) -> None:
        manifest.finished_at = utc_now()
        atomic_write_json(root / "run_manifest.json", manifest)
        try:
            self._write_prompt_rows(root, rows)
            render_audit(plan, manifest, root / "audit.html")
        except Exception as error:
            manifest.status = RunStatus.PARTIAL
            manifest.errors.append(
                {
                    "stage": "finalization",
                    "error_type": type(error).__name__,
                    "error": safe_error(error),
                }
            )
            atomic_write_json(root / "run_manifest.json", manifest)

    def run_plan(self, plan: CanvasPlan, policy: ExecutionPolicy) -> RunRecord:
        return self._run_plan(plan, policy, planning_calls=0)

    def _run_plan(
        self, plan: CanvasPlan, policy: ExecutionPolicy, *, planning_calls: int
    ) -> RunRecord:
        plan = CanvasPlan.model_validate(plan.model_dump(mode="json"))
        self._preflight(plan, policy)
        fingerprint = self.execution_sha256(plan, policy)
        root = self.runs_dir / f"run-{fingerprint[:16]}"
        root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(root / ".run.lock")):
            return self._run_plan_locked(plan, policy, root, fingerprint, planning_calls)

    def _run_plan_locked(
        self,
        plan: CanvasPlan,
        policy: ExecutionPolicy,
        root: Path,
        fingerprint: str,
        planning_calls: int,
    ) -> RunRecord:
        compiled = self.compile_workflow(plan, policy)
        manifest_path = root / "run_manifest.json"
        if manifest_path.is_file():
            previous = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            if previous.execution_sha256 != fingerprint:
                raise ResumeConflict("Run directory contains a different execution identity")
        for directory in ("assets", "videos/shots", "receipts"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        atomic_write_json(root / "canvas_plan.json", plan)
        atomic_write_json(root / "workflow.json", compiled.workflow)
        atomic_write_json(root / "workflow_api.json", compiled.api_workflow)
        manifest = RunManifest(
            run_id=root.name,
            plan_id=plan.plan_id,
            status=RunStatus.RUNNING,
            input_sha256=plan.input_sha256,
            policy=policy,
            run_root=str(root),
            execution_sha256=fingerprint,
            plan_sha256=plan_sha256(plan),
            composition_sha256=self._plugin_registry.composition_sha256
            if self._plugin_registry
            else None,
            plugins=[item.plugin_id for item in self._plugin_registry.snapshots()]
            if self._plugin_registry
            else [],
            call_counts={"planning": planning_calls, "fact_search": 0, "visual_search": 0},
        )
        rows: list[dict[str, Any]] = []
        atomic_write_json(manifest_path, manifest)
        try:
            if policy.mode == ExecutionMode.PLAN_ONLY:
                manifest.status = RunStatus.COMPLETE
            else:
                generated, rows = self._execute_assets(plan, policy, root, manifest)
                if policy.mode == ExecutionMode.FULL:
                    self._execute_videos(plan, root, manifest, generated, rows)
                manifest.status = RunStatus.PARTIAL if manifest.errors else RunStatus.COMPLETE
            self._execute_evaluation(plan, root, manifest)
        except Exception as error:
            manifest.status = RunStatus.PARTIAL if manifest.artifacts else RunStatus.FAILED
            manifest.errors.append(
                {
                    "stage": "execution",
                    "error_type": type(error).__name__,
                    "error": safe_error(error),
                }
            )
        except BaseException:
            manifest.status = RunStatus.FAILED
            manifest.errors.append(
                {
                    "stage": "execution",
                    "error_type": "InterruptedRun",
                    "error": "Execution was interrupted; inspect receipts before resuming.",
                }
            )
            raise
        finally:
            self._finish_run(plan, manifest, root, rows)
        return RunRecord(
            run_id=root.name, root=root, manifest=manifest, plan=plan, compiled_workflow=compiled
        )

    def complete_videos(self, run_root: str | Path, policy: ExecutionPolicy) -> RunRecord:
        """Continue an assets-only run with video generation in the same run root.

        This is deliberately separate from :meth:`run_plan`: changing an execution
        policy changes a normal run id, while a paid-video continuation must reuse
        the already-audited images instead of generating them a second time.
        """
        root = Path(run_root).expanduser().resolve(strict=True)
        with FileLock(str(root / ".run.lock")):
            return self._complete_videos_locked(root, policy)

    def _complete_videos_locked(self, root: Path, policy: ExecutionPolicy) -> RunRecord:
        plan_path = root / "canvas_plan.json"
        manifest_path = root / "run_manifest.json"
        prompts_path = root / "prompts.jsonl"
        if not plan_path.is_file() or not manifest_path.is_file():
            raise ProviderError(f"Not a StoryCanvas run root: {root}")
        if policy.mode != ExecutionMode.FULL or not policy.allow_paid_video:
            raise PolicyViolation(
                "Video continuation requires mode='full' and allow_paid_video=true"
            )

        plan = CanvasPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        recorded_root = Path(manifest.run_root).expanduser()
        if manifest.run_id != root.name:
            raise ProviderError("Run directory name does not match the persisted manifest")
        if manifest.plan_sha256 and manifest.plan_sha256 != plan_sha256(plan):
            raise ResumeConflict("Plan content changed since the assets run")
        if manifest.plan_id != plan.plan_id or manifest.input_sha256 != plan.input_sha256:
            raise ProviderError("Plan identity does not match the persisted manifest")
        self._preflight(plan, policy, videos_only=True)

        expected_asset_ids = {
            *(asset.asset_id for asset in plan.shared_assets),
            *(shot.keyframe_asset_id for shot in plan.shots),
        }
        generated: dict[str, ArtifactRecord] = {}
        preserved_artifacts: list[ArtifactRecord] = []
        for artifact in manifest.artifacts:
            if artifact.artifact_id == "story-video" or artifact.artifact_id.endswith("-video"):
                continue
            preserved_artifacts.append(artifact)
            if artifact.artifact_id not in expected_asset_ids:
                continue
            path = artifact_path(artifact.path, root, recorded_root)
            artifact.path = str(path)
            if (
                artifact.kind != "image"
                or artifact.provenance != Provenance.IMAGE_GENERATION
                or not path.is_file()
                or sha256_file(path) != artifact.sha256
            ):
                raise ProviderError(
                    f"Existing image failed continuation validation: {artifact.artifact_id}"
                )
            generated[artifact.artifact_id] = artifact
        missing = sorted(expected_asset_ids - generated.keys())
        if missing:
            raise ProviderError(
                "Video continuation requires all validated images; missing: " + ", ".join(missing)
            )

        prompt_rows: list[dict[str, Any]] = []
        if prompts_path.is_file():
            for line in prompts_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if not str(row.get("asset_id", "")).endswith("-video"):
                    prompt_rows.append(row)

        compiled = self.compile_workflow(plan, policy)
        atomic_write_json(root / "workflow.json", compiled.workflow)
        atomic_write_json(root / "workflow_api.json", compiled.api_workflow)
        manifest.policy = policy
        manifest.run_root = str(root)
        manifest.status = RunStatus.RUNNING
        manifest.finished_at = None
        manifest.artifacts = preserved_artifacts
        manifest.call_counts["video_generation"] = 0
        atomic_write_json(manifest_path, manifest)

        try:
            # Previous errors remain inspectable, but no longer determine this attempt's result.
            old_error_count = len(manifest.errors)
            self._execute_videos(plan, root, manifest, generated, prompt_rows)
            manifest.status = (
                RunStatus.COMPLETE if len(manifest.errors) == old_error_count else RunStatus.PARTIAL
            )
        except Exception as error:
            manifest.status = RunStatus.PARTIAL
            manifest.errors.append(
                {"stage": "video", "error_type": type(error).__name__, "error": safe_error(error)}
            )
        except BaseException:
            manifest.status = RunStatus.FAILED
            manifest.errors.append(
                {
                    "stage": "video",
                    "error_type": "InterruptedRun",
                    "error": "Video continuation was interrupted; existing task receipts are authoritative.",
                }
            )
            raise
        finally:
            self._finish_run(plan, manifest, root, prompt_rows)
        return RunRecord(
            run_id=manifest.run_id,
            root=root,
            manifest=manifest,
            plan=plan,
            compiled_workflow=compiled,
        )

    def run(
        self, value: ShotInput | StoryInput, policy: ExecutionPolicy | None = None
    ) -> RunRecord:
        selected_policy = policy or ExecutionPolicy()
        return self._run_plan(self.plan(value, selected_policy), selected_policy, planning_calls=1)
