from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .service import (
    PlanRequest,
    RunRequest,
    StoryCanvasService,
    WorkflowRequest,
    public_error,
)


def create_app(service: StoryCanvasService | None = None) -> FastAPI:
    selected = service or StoryCanvasService(
        Path(os.getenv("STORYCANVAS_SERVICE_DIR", "./runs/.service"))
    )
    app = FastAPI(title="StoryCanvas Harness", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "storycanvas-harness"}

    @app.post("/storycanvas/v1/plans")
    def create_plan(request: PlanRequest) -> dict[str, object]:
        try:
            plan = selected.create_plan(request)
            return plan.model_dump(mode="json", exclude_none=True)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.get("/storycanvas/v1/plans/{plan_id}")
    def get_plan(plan_id: str) -> dict[str, object]:
        try:
            return selected.get_plan(plan_id).model_dump(mode="json", exclude_none=True)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.post("/storycanvas/v1/workflows")
    def compile_workflow(request: WorkflowRequest) -> dict[str, object]:
        try:
            return selected.compile(request)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.post("/storycanvas/v1/runs", status_code=202)
    def start_run(request: RunRequest) -> dict[str, object]:
        try:
            return selected.start_run(request)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.get("/storycanvas/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            return selected.get_run(run_id)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.get("/storycanvas/v1/runs/{run_id}/events")
    def get_events(run_id: str) -> list[dict[str, object]]:
        return selected.events(run_id)

    return app


app = create_app()
