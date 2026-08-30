from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.errors import PluginError
from storycanvas_harness.plugins import (
    PluginContext,
    PluginManifest,
    PluginRegistry,
    PluginStatus,
    ServicePlugin,
    StoryCanvasProfile,
    build_builtin_registry,
    load_pack,
    load_plugin_manifest,
    load_profile,
    load_requested_plugin_factories,
)
from storycanvas_harness.protocol import (
    EVALUATION_RUN,
    IMAGE_GENERATE,
    IMAGE_PROMPT_COMPILE,
    STORY_PLAN,
)
from storycanvas_harness.schemas import (
    ArtifactRecord,
    CanvasPlan,
    ExecutionMode,
    ExecutionPolicy,
    ProviderReceipt,
    RunManifest,
    RunStatus,
    ShotInput,
    StoryInput,
)
from storycanvas_harness.utils import atomic_write_json, sha256_file, sha256_json

ROOT = Path(__file__).resolve().parents[1]


def test_basic_profile_activates_and_resolves_bound_services(tmp_path: Path) -> None:
    profile = load_profile(ROOT / "profiles" / "basic.json")
    registry = build_builtin_registry(profile, root=tmp_path)
    try:
        assert registry.provider_id(STORY_PLAN) == "storycanvas.director.basic"
        assert registry.provider_id(IMAGE_GENERATE) == "storycanvas.image.mock"
        assert all(item.status == PluginStatus.ACTIVE for item in registry.snapshots())
    finally:
        registry.dispose_all()
    assert all(item.status == PluginStatus.DISPOSED for item in registry.snapshots())


def test_profile_composes_existing_engine_without_environment_switches(tmp_path: Path) -> None:
    with StoryCanvas.from_profile(
        ROOT / "profiles" / "basic.json", runs_dir=tmp_path / "runs"
    ) as canvas:
        record = canvas.run(
            ShotInput(prompt="A paper observatory opens beneath a fictional violet sky."),
            ExecutionPolicy(mode=ExecutionMode.ASSETS, max_image_calls=2),
        )
        assert record.manifest.status == RunStatus.COMPLETE
        assert canvas.director.name == "deterministic"
        assert canvas.image_provider is not None
        assert canvas.image_provider.name == "mock"
        assert canvas.workflow_renderer is not None
        assert canvas.workflow_renderer.name == "comfyui"


def test_manifest_must_match_registered_services(tmp_path: Path) -> None:
    registry = PluginRegistry(root=tmp_path)
    plugin = ServicePlugin(
        manifest=PluginManifest(
            plugin_id="example.bad",
            version="0.1.0",
            provides=["example.one"],
        ),
        services={"example.two": object()},
    )
    with pytest.raises(PluginError, match="service mismatch"):
        registry.register(plugin)


def test_ambiguous_capability_requires_profile_binding(tmp_path: Path) -> None:
    registry = PluginRegistry(root=tmp_path)
    for suffix in ("a", "b"):
        registry.register(
            ServicePlugin(
                manifest=PluginManifest(
                    plugin_id=f"example.{suffix}",
                    version="0.1.0",
                    provides=["example.transform"],
                ),
                services={"example.transform": object()},
            )
        )
    registry.activate_all()
    try:
        with pytest.raises(PluginError, match="ambiguous"):
            registry.resolve_service("example.transform")
    finally:
        registry.dispose_all()


def test_profile_permission_allowlist_blocks_plugin_activation(tmp_path: Path) -> None:
    registry = PluginRegistry(root=tmp_path, allowed_permissions=[])
    registry.register(
        ServicePlugin(
            manifest=PluginManifest(
                plugin_id="example.networked",
                version="0.1.0",
                provides=["example.service"],
                permissions=["network:https"],
            ),
            services={"example.service": object()},
        )
    )
    with pytest.raises(PluginError, match="not allowed by the Profile"):
        registry.activate_all()


@dataclass(slots=True)
class TrackingPlugin:
    manifest: PluginManifest
    services: Mapping[str, Any]
    events: list[str] = field(default_factory=list)

    def start(self, context: PluginContext) -> None:
        self.events.append(f"start:{self.manifest.plugin_id}:{context.root.name}")

    def stop(self) -> None:
        self.events.append(f"stop:{self.manifest.plugin_id}")


@dataclass(slots=True)
class FailingPlugin(TrackingPlugin):
    def start(self, context: PluginContext) -> None:
        del context
        raise RuntimeError("intentional startup failure")


def test_required_capability_controls_lifecycle_order(tmp_path: Path) -> None:
    registry = PluginRegistry(root=tmp_path)
    consumer = TrackingPlugin(
        manifest=PluginManifest(
            plugin_id="example.consumer",
            version="0.1.0",
            provides=["example.output"],
            requires=["example.input"],
        ),
        services={"example.output": object()},
    )
    provider = TrackingPlugin(
        manifest=PluginManifest(
            plugin_id="example.provider",
            version="0.1.0",
            provides=["example.input"],
        ),
        services={"example.input": object()},
    )
    registry.register(consumer)
    registry.register(provider)
    registry.activate_all()
    assert provider.events[0].startswith("start:example.provider")
    assert consumer.events[0].startswith("start:example.consumer")
    registry.dispose_all()
    assert consumer.events[-1] == "stop:example.consumer"
    assert provider.events[-1] == "stop:example.provider"


def test_start_failure_disposes_already_active_plugins(tmp_path: Path) -> None:
    registry = PluginRegistry(root=tmp_path)
    provider = TrackingPlugin(
        manifest=PluginManifest(
            plugin_id="a.provider",
            version="0.1.0",
            provides=["example.input"],
        ),
        services={"example.input": object()},
    )
    failing = FailingPlugin(
        manifest=PluginManifest(
            plugin_id="b.failure",
            version="0.1.0",
            provides=["example.output"],
            requires=["example.input"],
        ),
        services={"example.output": object()},
    )
    registry.register(provider)
    registry.register(failing)
    with pytest.raises(PluginError, match="intentional startup failure"):
        registry.activate_all()
    assert provider.events[-1] == "stop:a.provider"
    snapshots = {item.plugin_id: item for item in registry.snapshots()}
    assert snapshots["a.provider"].status == PluginStatus.DISPOSED
    assert snapshots["b.failure"].status == PluginStatus.FAILED


def test_profile_schema_rejects_unknown_api_version(tmp_path: Path) -> None:
    profile = json.loads((ROOT / "profiles" / "basic.json").read_text(encoding="utf-8"))
    profile["schema_version"] = "storycanvas/profile/v999"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported profile API"):
        load_profile(path)


def test_profile_rejects_unselected_plugin_configuration() -> None:
    with pytest.raises(ValueError, match="unselected Plugins"):
        StoryCanvasProfile(
            name="bad-config",
            plugins=["example.selected"],
            plugin_config={"example.unselected": {}},
        )


def test_discovery_imports_only_profile_requested_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[str] = []

    class FakeEntryPoint:
        def __init__(self, name: str) -> None:
            self.name = name

        def load(self) -> object:
            loaded.append(self.name)
            return lambda: ServicePlugin(
                manifest=PluginManifest(
                    plugin_id=self.name,
                    version="0.1.0",
                    provides=["example.service"],
                ),
                services={"example.service": object()},
            )

    monkeypatch.setattr(
        "storycanvas_harness.plugins.discovery.entry_points",
        lambda **_: [FakeEntryPoint("community.requested"), FakeEntryPoint("community.ignored")],
    )
    factories = load_requested_plugin_factories(["community.requested"])
    assert list(factories) == ["community.requested"]
    assert loaded == ["community.requested"]
    assert factories["community.requested"]().manifest.plugin_id == "community.requested"


def test_catalog_key_must_match_factory_manifest_id(tmp_path: Path) -> None:
    profile = StoryCanvasProfile(
        name="factory-id-check",
        plugins=["community.expected"],
        bindings={"example.service": "community.expected"},
    )
    with pytest.raises(PluginError, match="returned manifest id"):
        build_builtin_registry(
            profile,
            root=tmp_path,
            discover_installed=False,
            extra_catalog={
                "community.expected": lambda: ServicePlugin(
                    manifest=PluginManifest(
                        plugin_id="community.different",
                        version="0.1.0",
                        provides=["example.service"],
                    ),
                    services={"example.service": object()},
                )
            },
        )


def test_checked_in_pack_matches_profile_and_template_manifest() -> None:
    pack = load_pack(ROOT / "packs" / "basic" / "pack.toml")
    profile = load_profile(ROOT / "profiles" / "basic.json")
    template = load_plugin_manifest(ROOT / "plugins" / "template" / "storycanvas.plugin.toml")
    assert pack.plugins == profile.plugins
    assert pack.api_version == "storycanvas/pack/v1"
    assert template.plugin_id == "community.example-plugin"
    assert template.provides == [IMAGE_PROMPT_COMPILE]
    assert template.requires == [STORY_PLAN]


class PromptSuffixProcessor:
    name = "prompt-suffix"
    model = "test-v1"

    def transform(
        self,
        plan: CanvasPlan,
        source: ShotInput | StoryInput,
        policy: ExecutionPolicy,
    ) -> CanvasPlan:
        del source, policy
        plan.shots[0].image_prompt += " Use a restrained cyan palette."
        return plan


def test_prompt_processor_plugin_changes_typed_plan_and_run_identity(tmp_path: Path) -> None:
    processor_id = "community.prompt-suffix"
    profile = StoryCanvasProfile(
        name="prompt-hook",
        plugins=["storycanvas.director.basic", processor_id],
        bindings={
            STORY_PLAN: "storycanvas.director.basic",
            IMAGE_PROMPT_COMPILE: processor_id,
        },
    )
    registry = build_builtin_registry(
        profile,
        root=tmp_path,
        discover_installed=False,
        extra_catalog={
            processor_id: lambda: ServicePlugin(
                manifest=PluginManifest(
                    plugin_id=processor_id,
                    version="0.1.0",
                    provides=[IMAGE_PROMPT_COMPILE],
                    requires=[STORY_PLAN],
                ),
                services={IMAGE_PROMPT_COMPILE: PromptSuffixProcessor()},
            )
        },
    )
    with StoryCanvas.from_registry(registry, runs_dir=tmp_path / "runs") as canvas:
        plan = canvas.plan(
            ShotInput(prompt="A fictional blue paper boat crosses a glass pond."),
            ExecutionPolicy(),
        )
        record = canvas.run_plan(plan, ExecutionPolicy())
    assert plan.shots[0].image_prompt.endswith("Use a restrained cyan palette.")
    assert record.manifest.composition_sha256 is not None
    assert processor_id in record.manifest.plugins


class JsonEvaluator:
    name = "json-evaluator"
    model = "test-v1"

    def evaluate(
        self,
        plan: CanvasPlan,
        manifest: RunManifest,
        run_root: Path,
    ) -> list[ArtifactRecord]:
        del manifest
        output = run_root / "evaluation" / "summary.json"
        atomic_write_json(output, {"plan_id": plan.plan_id, "score": 1.0})
        return [
            ArtifactRecord(
                artifact_id="evaluation-summary",
                kind="json",
                path=str(output),
                sha256=sha256_file(output),
                receipt=ProviderReceipt(
                    provider=self.name,
                    model=self.model,
                    operation="evaluation",
                    request_sha256=sha256_json({"plan_id": plan.plan_id}),
                ),
            )
        ]


def test_evaluation_plugin_outputs_validated_run_artifact(tmp_path: Path) -> None:
    evaluator_id = "community.json-evaluator"
    profile = StoryCanvasProfile(
        name="evaluation-hook",
        plugins=["storycanvas.director.basic", evaluator_id],
        bindings={
            STORY_PLAN: "storycanvas.director.basic",
            EVALUATION_RUN: evaluator_id,
        },
        allow_permissions=["filesystem:run-dir"],
    )
    registry = build_builtin_registry(
        profile,
        root=tmp_path,
        discover_installed=False,
        extra_catalog={
            evaluator_id: lambda: ServicePlugin(
                manifest=PluginManifest(
                    plugin_id=evaluator_id,
                    version="0.1.0",
                    provides=[EVALUATION_RUN],
                    requires=[STORY_PLAN],
                    permissions=["filesystem:run-dir"],
                ),
                services={EVALUATION_RUN: JsonEvaluator()},
            )
        },
    )
    with StoryCanvas.from_registry(registry, runs_dir=tmp_path / "runs") as canvas:
        record = canvas.run(
            ShotInput(prompt="A fictional brass kite turns above a paper town."),
            ExecutionPolicy(),
        )
    assert record.manifest.status == RunStatus.COMPLETE
    assert record.manifest.call_counts["evaluation"] == 1
    assert record.manifest.call_counts["evaluation_artifacts"] == 1
    assert any(item.artifact_id == "evaluation-summary" for item in record.manifest.artifacts)
