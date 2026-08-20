from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .service import (
    PlanRequest,
    RunRequest,
    StoryCanvasService,
    WorkflowRequest,
    public_error,
)

_REGISTERED = False
_SERVICE: StoryCanvasService | None = None


def _service() -> StoryCanvasService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = StoryCanvasService(Path(os.getenv("STORYCANVAS_SERVICE_DIR", "./runs/.service")))
    return _SERVICE


def register_comfy_routes() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    async def execute(call: Any, request: Any) -> web.Response:
        try:
            result = await asyncio.to_thread(call, request)
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json", exclude_none=True)
            return web.json_response(result)
        except Exception as error:
            status, payload = public_error(error)
            return web.json_response(payload, status=status)

    @routes.post("/storycanvas/v1/plans")
    async def create_plan(request: web.Request) -> web.Response:
        try:
            payload = PlanRequest.model_validate(await request.json())
        except Exception as error:
            status, detail = public_error(error)
            return web.json_response(detail, status=status)
        return await execute(_service().create_plan, payload)

    @routes.get("/storycanvas/v1/plans/{plan_id}")
    async def get_plan(request: web.Request) -> web.Response:
        return await execute(_service().get_plan, request.match_info["plan_id"])

    @routes.post("/storycanvas/v1/workflows")
    async def compile_workflow(request: web.Request) -> web.Response:
        try:
            payload = WorkflowRequest.model_validate(await request.json())
        except Exception as error:
            status, detail = public_error(error)
            return web.json_response(detail, status=status)
        return await execute(_service().compile, payload)

    @routes.post("/storycanvas/v1/runs")
    async def start_run(request: web.Request) -> web.Response:
        try:
            payload = RunRequest.model_validate(await request.json())
        except Exception as error:
            status, detail = public_error(error)
            return web.json_response(detail, status=status)
        return await execute(_service().start_run, payload)

    @routes.get("/storycanvas/v1/runs/{run_id}")
    async def get_run(request: web.Request) -> web.Response:
        return await execute(_service().get_run, request.match_info["run_id"])

    @routes.get("/storycanvas/v1/runs/{run_id}/events")
    async def get_events(request: web.Request) -> web.Response:
        return await execute(_service().events, request.match_info["run_id"])

    _REGISTERED = True
