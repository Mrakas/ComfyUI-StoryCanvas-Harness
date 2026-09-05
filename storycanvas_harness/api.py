from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .service import (
    PlanRequest,
    RunRequest,
    StoryCanvasService,
    WorkflowRequest,
    public_error,
)


def create_app(service: StoryCanvasService | None = None) -> FastAPI:
    selected = service
    mutex = Lock()

    def get_service() -> StoryCanvasService:
        nonlocal selected
        with mutex:
            if selected is None:
                selected = StoryCanvasService(
                    Path(os.getenv("STORYCANVAS_SERVICE_DIR", "./runs/.service"))
                )
            return selected

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            yield
        finally:
            if selected is not None:
                selected.close()

    app = FastAPI(title="StoryCanvas Harness", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request
        from .utils import safe_error

        errors = [
            ".".join(str(part) for part in item["loc"]) + ": " + item["msg"]
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={
                "detail": {"error": safe_error("; ".join(errors)), "error_type": "ValidationError"}
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "storycanvas-harness"}

    @app.post("/storycanvas/v1/plans")
    def create_plan(request: PlanRequest) -> dict[str, object]:
        try:
            plan = get_service().create_plan(request)
            return plan.model_dump(mode="json", exclude_none=True)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.get("/storycanvas/v1/plans/{plan_id}")
    def get_plan(plan_id: str) -> dict[str, object]:
        try:
            return get_service().get_plan(plan_id).model_dump(mode="json", exclude_none=True)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.post("/storycanvas/v1/workflows")
    def compile_workflow(request: WorkflowRequest) -> dict[str, object]:
        try:
            return get_service().compile(request)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.post("/storycanvas/v1/runs", status_code=202)
    def start_run(request: RunRequest) -> dict[str, object]:
        try:
            return get_service().start_run(request)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.get("/storycanvas/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            return get_service().get_run(run_id)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    @app.get("/storycanvas/v1/runs/{run_id}/events")
    def get_events(run_id: str) -> list[dict[str, object]]:
        try:
            return get_service().events(run_id)
        except Exception as error:
            status, detail = public_error(error)
            raise HTTPException(status_code=status, detail=detail) from error

    return app


app = create_app()
