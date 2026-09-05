"""Shared content identities for plans and host execution requests."""

from __future__ import annotations

import re
from typing import Any

from .schemas import CanvasPlan
from .utils import sha256_json


def plan_content(plan: CanvasPlan) -> dict[str, Any]:
    value = plan.model_dump(mode="json", exclude={"created_at", "plan_id"}, exclude_none=True)
    value["shots"] = sorted(value["shots"], key=lambda shot: shot["order"])
    # Ephemeral transport traces describe one call, not the requested computation.
    for key in ("planning_provider", "image_provider", "video_provider"):
        descriptor = value.get(key)
        if descriptor and descriptor.get("endpoint_kind"):
            descriptor["endpoint_kind"] = re.sub(
                r";?(?:thread|turn|item)(?:_id)?=[^;]+", "", descriptor["endpoint_kind"]
            )
    return value


def plan_sha256(plan: CanvasPlan) -> str:
    return sha256_json(plan_content(plan))


def provider_identity(provider: Any) -> dict[str, Any] | None:
    if provider is None:
        return None
    config = {
        key: str(getattr(provider, key))
        for key in ("base_url", "revision", "resolution", "duration", "ratio", "reasoning_effort")
        if getattr(provider, key, None) is not None
    }
    client = getattr(provider, "client", None)
    if client is not None and getattr(client, "base_url", None) is not None:
        config["client_base_url"] = str(client.base_url)
    if client is not None and getattr(client, "reasoning_effort", None) is not None:
        config["reasoning_effort"] = str(client.reasoning_effort)
    return {
        "name": provider.name,
        "model": provider.model,
        "implementation": f"{type(provider).__module__}.{type(provider).__qualname__}",
        "config_sha256": sha256_json(config),
    }
