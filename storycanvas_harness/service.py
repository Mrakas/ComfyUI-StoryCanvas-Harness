from __future__ import annotations

import json
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Literal, cast

from pydantic import Field

from .engine import StoryCanvas
from .errors import StoryCanvasError
from .schemas import (
    CanvasPlan,
    ExecutionPolicy,
    ShotInput,
    StoryInput,
    StrictModel,
)
from .utils import atomic_write_json, atomic_write_text, ensure_safe_id, sha256_json, utc_now


class PlanRequest(StrictModel):
    input_kind: Literal["shot", "story"]
    payload: dict[str, Any]
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)


class WorkflowRequest(StrictModel):
    plan_id: str | None = None
    plan: CanvasPlan | None = None
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)


class RunRequest(StrictModel):
    plan_id: str | None = None
    plan: CanvasPlan | None = None
    policy: ExecutionPolicy


class StoryCanvasService:
    def __init__(self, root: Path, engine: StoryCanvas | None = None):
        self.root = root
        self.engine = engine or StoryCanvas.from_environment(runs_dir=root / "runs")
        self.plan_dir = root / "plans"
        self.job_dir = root / "jobs"
        self.event_dir = root / "events"
        for directory in (self.plan_dir, self.job_dir, self.event_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._event_lock = Lock()

    @staticmethod
    def parse_input(request: PlanRequest) -> ShotInput | StoryInput:
        return (
            ShotInput.model_validate(request.payload)
            if request.input_kind == "shot"
            else StoryInput.model_validate(request.payload)
        )

    def create_plan(self, request: PlanRequest) -> CanvasPlan:
        plan = self.engine.plan(self.parse_input(request), request.policy)
        atomic_write_json(self.plan_dir / f"{plan.plan_id}.json", plan)
        return plan

    def get_plan(self, plan_id: str) -> CanvasPlan:
        ensure_safe_id(plan_id, label="plan_id")
        path = self.plan_dir / f"{plan_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Unknown plan: {plan_id}")
        return CanvasPlan.model_validate_json(path.read_text(encoding="utf-8"))

    def compile(self, request: WorkflowRequest) -> dict[str, Any]:
        plan = request.plan or (self.get_plan(request.plan_id) if request.plan_id else None)
        if plan is None:
            raise ValueError("plan or plan_id is required")
        compiled = self.engine.compile_workflow(plan, request.policy)
        output = self.root / "workflows" / f"{plan.plan_id}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output, compiled.workflow)
        atomic_write_json(output.with_name(f"{plan.plan_id}.api.json"), compiled.api_workflow)
        return compiled.model_dump(mode="json", exclude_none=True)

    def _event(self, job_id: str, event: dict[str, Any]) -> None:
        row = {"at": utc_now().isoformat(), **event}
        path = self.event_dir / f"{job_id}.jsonl"
        with self._event_lock:
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            atomic_write_text(path, existing + json.dumps(row, ensure_ascii=False) + "\n")

    def _run_job(self, job_id: str, request: RunRequest, plan: CanvasPlan) -> None:
        state_path = self.job_dir / f"{job_id}.json"
        state = {
            "job_id": job_id,
            "plan_id": plan.plan_id,
            "status": "running",
            "started_at": utc_now().isoformat(),
        }
        atomic_write_json(state_path, state)
        self._event(job_id, {"type": "run_started", "plan_id": plan.plan_id})
        try:
            record = self.engine.run_plan(plan, request.policy)
            state.update(
                status=record.manifest.status.value,
                run_id=record.run_id,
                run_root=str(record.root),
                finished_at=utc_now().isoformat(),
            )
            self._event(
                job_id,
                {"type": "run_finished", "status": state["status"], "run_id": record.run_id},
            )
        except Exception as error:
            state.update(
                status="failed",
                error_type=type(error).__name__,
                error=str(error),
                finished_at=utc_now().isoformat(),
            )
            self._event(
                job_id,
                {"type": "run_failed", "error_type": type(error).__name__, "error": str(error)},
            )
        atomic_write_json(state_path, state)

    def start_run(self, request: RunRequest) -> dict[str, Any]:
        plan = request.plan or (self.get_plan(request.plan_id) if request.plan_id else None)
        if plan is None:
            raise ValueError("plan or plan_id is required")
        job_id = f"job-{sha256_json({'plan_id': plan.plan_id, 'policy': request.policy})[:16]}"
        state_path = self.job_dir / f"{job_id}.json"
        if state_path.is_file():
            existing = cast(dict[str, Any], json.loads(state_path.read_text(encoding="utf-8")))
            if existing.get("status") in {"queued", "running", "complete", "partial"}:
                return existing
        queued = {
            "job_id": job_id,
            "plan_id": plan.plan_id,
            "status": "queued",
            "created_at": utc_now().isoformat(),
        }
        atomic_write_json(state_path, queued)
        Thread(target=self._run_job, args=(job_id, request, plan), daemon=True).start()
        return queued

    def get_run(self, job_id: str) -> dict[str, Any]:
        ensure_safe_id(job_id, label="job_id")
        path = self.job_dir / f"{job_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Unknown run job: {job_id}")
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def events(self, job_id: str) -> list[dict[str, Any]]:
        ensure_safe_id(job_id, label="job_id")
        path = self.event_dir / f"{job_id}.jsonl"
        if not path.is_file():
            return []
        return [
            cast(dict[str, Any], json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]


def public_error(error: Exception) -> tuple[int, dict[str, str]]:
    if isinstance(error, FileNotFoundError):
        return 404, {"error": str(error), "error_type": type(error).__name__}
    if isinstance(error, (ValueError, StoryCanvasError)):
        return 400, {"error": str(error), "error_type": type(error).__name__}
    return 500, {"error": "Internal StoryCanvas error", "error_type": type(error).__name__}
