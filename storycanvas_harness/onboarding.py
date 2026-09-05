"""Credential-free diagnostics and a packaged first-run example."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from .canvas_export import export_story_canvas
from .config import RuntimeSettings
from .engine import StoryCanvas
from .errors import ProviderError
from .plugins import StoryCanvasProfile, build_builtin_registry, load_profile
from .plugins.builtin import builtin_plugin_catalog
from .plugins.discovery import installed_entry_points
from .schemas import ExecutionMode, ExecutionPolicy, StoryInput


def diagnose(
    *,
    mode: ExecutionMode = ExecutionMode.PLAN_ONLY,
    profile_file: Path | None = None,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def check(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    check(
        "python",
        "ok" if (3, 10) <= sys.version_info[:2] < (3, 14) else "error",
        f"Python {sys.version.split()[0]}; supported: 3.10-3.13",
    )
    selected_dir = (
        Path(runs_dir if runs_dir is not None else os.environ.get("STORYCANVAS_RUNS_DIR", "runs"))
        .expanduser()
        .resolve()
    )
    ancestor = selected_dir
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    writable = ancestor.is_dir() and os.access(ancestor, os.W_OK)
    check(
        "runs_dir",
        "ok" if writable else "error",
        f"{selected_dir}"
        if writable
        else f"Choose a writable directory with --runs-dir: {selected_dir}",
    )
    for binary in ("ffmpeg", "ffprobe"):
        present = shutil.which(binary)
        check(
            binary,
            "ok" if present else ("error" if mode == ExecutionMode.FULL else "warning"),
            present
            or f"Install {binary} for video generation and validation; the image demo works without it.",
        )
    capabilities: set[str] = set()
    if profile_file is not None:
        profile = load_profile(profile_file)
        catalog = builtin_plugin_catalog()
        installed = installed_entry_points()
        check("profile", "ok", profile.name)
        for plugin_id in profile.plugins:
            if plugin_id in catalog:
                plugin = catalog[plugin_id]()
                denied = set(plugin.manifest.permissions) - set(profile.allow_permissions)
                check(
                    plugin_id,
                    "error" if denied else "ok",
                    f"Allow declared permissions in the Profile: {sorted(denied)}"
                    if denied
                    else "Built-in plugin available; not started.",
                )
                capabilities.update(plugin.manifest.provides)
                for capability, bound in profile.bindings.items():
                    if bound == plugin_id and capability not in plugin.manifest.provides:
                        check("binding", "error", f"{plugin_id} does not provide {capability}")
            elif plugin_id in installed:
                check(
                    plugin_id,
                    "warning",
                    "Installed entry point; capabilities and configuration require runtime validation. Plugin code was not imported.",
                )
            else:
                check(plugin_id, "error", "Install the package providing this Profile plugin.")
        for capability in (
            "story.plan",
            *(["media.image.generate"] if mode != ExecutionMode.PLAN_ONLY else []),
            *(["media.video.generate"] if mode == ExecutionMode.FULL else []),
        ):
            selected_plugin = profile.bindings.get(capability)
            if capability not in capabilities and not (
                selected_plugin and selected_plugin in installed
            ):
                check(
                    capability,
                    "error",
                    f"Add a plugin and binding for {capability} to the Profile.",
                )
        provider_mode = "profile"
    else:
        settings = RuntimeSettings.from_environment(runs_dir=selected_dir)
        provider_mode = settings.provider_mode
        check("provider_mode", "ok", provider_mode)
        if provider_mode == "mock":
            check(
                "providers",
                "ok",
                "Deterministic local providers; no API keys, network, or GPU required.",
            )
        elif provider_mode == "openai":
            configured = bool(os.getenv("OPENAI_API_KEY"))
            check(
                "openai",
                "ok" if configured else ("warning" if mode == ExecutionMode.PLAN_ONLY else "error"),
                "OPENAI_API_KEY is set; credentials were not sent or verified."
                if configured
                else "Set OPENAI_API_KEY for real planning/images, or use storycanvas demo. Without a key, planning is an offline preview.",
            )
        else:
            check(
                "codex_enabled",
                "ok" if settings.codex_enabled else "error",
                "Codex mode enabled."
                if settings.codex_enabled
                else "Set STORYCANVAS_CODEX_ENABLED=true to select Codex explicitly.",
            )
            check(
                "codex_sdk",
                "ok" if importlib.util.find_spec("openai_codex") else "error",
                "Install the optional SDK with uv sync --extra codex.",
            )
            check(
                "codex_cli",
                "ok" if shutil.which(settings.codex_bin) else "error",
                f"Executable: {settings.codex_bin}; set STORYCANVAS_CODEX_BIN if needed.",
            )
            check(
                "codex_account",
                "warning",
                f"Login and model availability were not queried. Requested model: {settings.codex_model}; effort: {settings.codex_effort}. Use codex login if authentication is missing.",
            )
        if mode == ExecutionMode.FULL and provider_mode != "mock":
            configured = bool(os.getenv("MINIMAX_H3_API_KEY") and os.getenv("MINIMAX_H3_BASE_URL"))
            check(
                "video_provider",
                "ok" if configured else "error",
                "Video configuration present; endpoint not contacted."
                if configured
                else "Set both MINIMAX_H3_API_KEY and MINIMAX_H3_BASE_URL for video.",
            )
    return {
        "ok": all(item["status"] != "error" for item in checks),
        "mode": mode.value,
        "provider_mode": provider_mode,
        "checks": checks,
        "network_calls": 0,
    }


def run_demo(output_dir: Path, *, with_video: bool = False) -> dict[str, Any]:
    root = output_dir.expanduser().resolve()
    resources = files("storycanvas_harness").joinpath("resources")
    profile = StoryCanvasProfile.model_validate_json(
        resources.joinpath("basic.json").read_text(encoding="utf-8")
    )
    payload = json.loads(resources.joinpath("demo_story.json").read_text(encoding="utf-8"))
    story = StoryInput.model_validate(payload["payload"])
    if with_video and (not shutil.which("ffmpeg") or not shutil.which("ffprobe")):
        raise ProviderError(
            "The video demo requires ffmpeg and ffprobe. Install them or run storycanvas demo without --with-video."
        )
    policy = ExecutionPolicy(
        mode=ExecutionMode.FULL if with_video else ExecutionMode.ASSETS,
        allow_paid_video=with_video,
        max_shots=3,
        max_image_calls=4,
        max_video_calls=3 if with_video else 0,
    )
    registry = build_builtin_registry(profile, root=root, discover_installed=False)
    with StoryCanvas.from_registry(registry, runs_dir=root / "runs") as canvas:
        record = canvas.run(story, policy)
    if record.manifest.status.value != "complete":
        raise ProviderError(f"Demo did not complete; inspect {record.root / 'run_manifest.json'}")
    viewer_dir = root / record.run_id
    report = export_story_canvas(record.root, viewer_dir)
    return {
        "status": record.manifest.status.value,
        "run_id": record.run_id,
        "run_dir": str(record.root),
        "manifest": str(record.root / "run_manifest.json"),
        "audit": str(record.root / "audit.html"),
        "viewer": str(viewer_dir / "index.html"),
        "mode": policy.mode.value,
        "media": report["counts"]["media"],
        "call_counts": record.manifest.call_counts,
        "network_calls": 0,
        "paid_calls": 0,
    }
