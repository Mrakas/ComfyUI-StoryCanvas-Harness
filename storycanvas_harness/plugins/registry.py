"""Dependency-aware capability registry with reversible plugin lifecycle."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ..errors import PluginError
from ..utils import sha256_json
from .api import (
    PluginContext,
    PluginSnapshot,
    PluginStatus,
    StoryCanvasPlugin,
)


class PluginRegistry:
    def __init__(
        self,
        *,
        root: Path,
        bindings: dict[str, str] | None = None,
        plugin_config: dict[str, dict[str, Any]] | None = None,
        allowed_permissions: list[str] | None = None,
    ) -> None:
        self.root = root
        self.bindings = bindings or {}
        self.plugin_config = plugin_config or {}
        self.allowed_permissions = set(allowed_permissions or [])
        self._plugins: dict[str, StoryCanvasPlugin] = {}
        self._status: dict[str, PluginStatus] = {}
        self._errors: dict[str, str] = {}
        self._activation_order: list[str] = []

    def register(self, plugin: StoryCanvasPlugin) -> None:
        plugin_id = plugin.manifest.plugin_id
        if plugin_id in self._plugins:
            raise PluginError(f"Duplicate plugin id: {plugin_id}")
        declared = set(plugin.manifest.provides)
        actual = set(plugin.services)
        if declared != actual:
            raise PluginError(
                f"Plugin {plugin_id} service mismatch: declared={sorted(declared)}, "
                f"actual={sorted(actual)}"
            )
        self._plugins[plugin_id] = plugin
        self._status[plugin_id] = PluginStatus.DISCOVERED

    def _providers(self) -> dict[str, list[str]]:
        providers: dict[str, list[str]] = defaultdict(list)
        for plugin_id, plugin in self._plugins.items():
            for capability in plugin.manifest.provides:
                providers[capability].append(plugin_id)
        return providers

    def validate_bindings(self) -> None:
        providers = self._providers()
        for capability, plugin_id in self.bindings.items():
            if plugin_id not in self._plugins:
                raise PluginError(f"Binding {capability!r} references unknown plugin {plugin_id!r}")
            if capability not in self._plugins[plugin_id].manifest.provides:
                raise PluginError(
                    f"Plugin {plugin_id!r} does not provide bound capability {capability!r}"
                )
        for plugin in self._plugins.values():
            denied_permissions = sorted(set(plugin.manifest.permissions) - self.allowed_permissions)
            if denied_permissions:
                raise PluginError(
                    f"Plugin {plugin.manifest.plugin_id!r} requests permissions not allowed "
                    f"by the Profile: {denied_permissions}"
                )
            for requirement in plugin.manifest.requires:
                if requirement not in providers:
                    raise PluginError(
                        f"Plugin {plugin.manifest.plugin_id!r} requires unavailable "
                        f"capability {requirement!r}"
                    )

    def activate_all(self) -> None:
        self.validate_bindings()
        pending = set(self._plugins)
        while pending:
            progress = False
            for plugin_id in sorted(pending):
                plugin = self._plugins[plugin_id]
                if not all(
                    self.has_capability(item, active_only=True) for item in plugin.manifest.requires
                ):
                    self._status[plugin_id] = PluginStatus.WAITING
                    continue
                self._status[plugin_id] = PluginStatus.LOADING
                try:
                    plugin.start(
                        PluginContext(
                            root=self.root,
                            config=self.plugin_config.get(plugin_id, {}),
                            metadata={"plugin_id": plugin_id},
                        )
                    )
                except Exception as error:
                    self._status[plugin_id] = PluginStatus.FAILED
                    self._errors[plugin_id] = f"{type(error).__name__}: {error}"
                    failure = PluginError(
                        f"Plugin {plugin_id!r} failed to start: {self._errors[plugin_id]}"
                    )
                    try:
                        self.dispose_all()
                    except PluginError as cleanup_error:
                        raise PluginError(
                            f"{failure}; cleanup also failed: {cleanup_error}"
                        ) from error
                    raise failure from error
                self._status[plugin_id] = PluginStatus.ACTIVE
                self._activation_order.append(plugin_id)
                pending.remove(plugin_id)
                progress = True
            if not progress:
                waiting = {
                    plugin_id: self._plugins[plugin_id].manifest.requires
                    for plugin_id in sorted(pending)
                }
                raise PluginError(
                    f"Plugin dependency cycle or unavailable active service: {waiting}"
                )

    def has_capability(self, capability: str, *, active_only: bool = False) -> bool:
        return any(
            capability in plugin.manifest.provides
            and (not active_only or self._status[plugin_id] == PluginStatus.ACTIVE)
            for plugin_id, plugin in self._plugins.items()
        )

    def provider_id(self, capability: str) -> str:
        candidates = [
            plugin_id
            for plugin_id, plugin in self._plugins.items()
            if capability in plugin.manifest.provides
            and self._status[plugin_id] == PluginStatus.ACTIVE
        ]
        bound = self.bindings.get(capability)
        if bound:
            if bound not in candidates:
                raise PluginError(
                    f"Bound plugin {bound!r} is not active for capability {capability!r}"
                )
            return bound
        if not candidates:
            raise PluginError(f"No active plugin provides capability {capability!r}")
        if len(candidates) > 1:
            raise PluginError(
                f"Capability {capability!r} is ambiguous; bind one of {sorted(candidates)}"
            )
        return candidates[0]

    def resolve_service(self, capability: str) -> Any:
        plugin_id = self.provider_id(capability)
        return self._plugins[plugin_id].services[capability]

    def dispose_all(self) -> None:
        errors: list[str] = []
        for plugin_id in reversed(self._activation_order):
            plugin = self._plugins[plugin_id]
            self._status[plugin_id] = PluginStatus.UNLOADING
            try:
                plugin.stop()
            except Exception as error:  # pragma: no cover - defensive cleanup path
                errors.append(f"{plugin_id}: {type(error).__name__}: {error}")
            self._status[plugin_id] = PluginStatus.DISPOSED
        self._activation_order.clear()
        if errors:
            raise PluginError("Plugin cleanup failed: " + "; ".join(errors))

    def snapshots(self) -> list[PluginSnapshot]:
        return [
            PluginSnapshot(
                plugin_id=plugin_id,
                version=plugin.manifest.version,
                status=self._status[plugin_id],
                provides=plugin.manifest.provides,
                requires=plugin.manifest.requires,
                permissions=plugin.manifest.permissions,
                error=self._errors.get(plugin_id),
            )
            for plugin_id, plugin in sorted(self._plugins.items())
        ]

    @property
    def composition_sha256(self) -> str:
        """Identity for selected Plugin versions, bindings, config, and permissions."""

        return sha256_json(
            {
                "plugins": [
                    self._plugins[plugin_id].manifest.model_dump(mode="json", exclude_none=True)
                    for plugin_id in sorted(self._plugins)
                ],
                "bindings": self.bindings,
                "plugin_config": self.plugin_config,
                "allowed_permissions": sorted(self.allowed_permissions),
            }
        )
