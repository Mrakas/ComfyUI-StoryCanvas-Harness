from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any, Literal, cast

from filelock import FileLock, Timeout
from pydantic import Field, model_validator

from .engine import StoryCanvas
from .errors import ResumeConflict, StoryCanvasError
from .schemas import CanvasPlan, ExecutionPolicy, SafeId, ShotInput, StoryInput, StrictModel
from .storage import append_jsonl, read_jsonl
from .utils import atomic_write_json, ensure_safe_id, safe_error, utc_now


class PlanRequest(StrictModel):
    input_kind: Literal["shot", "story"]
    payload: dict[str, Any]
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)


class PlanSelection(StrictModel):
    plan_id: SafeId | None = None
    plan: CanvasPlan | None = None

    @model_validator(mode="after")
    def require_one_plan(self) -> PlanSelection:
        if (self.plan is None) == (self.plan_id is None):
            raise ValueError("Provide exactly one of plan or plan_id")
        return self


class WorkflowRequest(PlanSelection):
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)


class RunRequest(PlanSelection):
    policy: ExecutionPolicy


class StoryCanvasService:
    def __init__(self, root: Path, engine: StoryCanvas | None = None):
        self.root = root.expanduser().resolve()
        self._engine = engine
        self._owns_engine = engine is None
        self._mutex = RLock()
        self._closing = False
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="storycanvas")
        self.plan_dir = self.root / "plans"
        self.job_dir = self.root / "jobs"
        self.event_dir = self.root / "events"
        self.request_dir = self.root / "requests"
        for directory in (self.plan_dir, self.job_dir, self.event_dir, self.request_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def engine(self) -> StoryCanvas:
        with self._mutex:
            if self._engine is None:
                self._engine = StoryCanvas.from_environment(runs_dir=self.root / "runs")
            return self._engine

    def close(self) -> None:
        with self._mutex:
            self._closing = True
        self._pool.shutdown(wait=True)
        if self._owns_engine and self._engine is not None:
            self._engine.close()

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

    def _select_plan(self, request: PlanSelection) -> CanvasPlan:
        if request.plan is not None:
            return CanvasPlan.model_validate(request.plan.model_dump(mode="json"))
        if request.plan_id is None:
            raise ValueError("Provide exactly one of plan or plan_id")
        return self.get_plan(request.plan_id)

    def compile(self, request: WorkflowRequest) -> dict[str, Any]:
        plan = self._select_plan(request)
        # Default ComfyUI compilation is pure and requires no provider configuration.
        engine = self._engine or StoryCanvas()
        compiled = engine.compile_workflow(plan, request.policy)
        output = self.root / "workflows" / f"{plan.plan_id}-{compiled.workflow_sha256[:16]}.json"
        atomic_write_json(output, compiled.workflow)
        atomic_write_json(output.with_suffix(".api.json"), compiled.api_workflow)
        return compiled.model_dump(mode="json", exclude_none=True)

    def _event(self, job_id: str, event: dict[str, Any]) -> None:
        append_jsonl(self.event_dir / f"{job_id}.jsonl", {"at": utc_now().isoformat(), **event})

    def _job_lock(self, job_id: str) -> FileLock:
        # Ownership is handed from the submitting thread to its worker.
        return FileLock(str(self.job_dir / f"{job_id}.lock"), thread_local=False)

    def _run_job(self, job_id: str, request: RunRequest, plan: CanvasPlan, lock: FileLock) -> None:
        state_path = self.job_dir / f"{job_id}.json"
        state: dict[str, Any] = {"job_id": job_id, "plan_id": plan.plan_id}
        try:
            state.update(self._read_state(job_id))
            state.update(status="running", started_at=utc_now().isoformat())
            atomic_write_json(state_path, state)
            self._event(job_id, {"type": "run_started", "plan_id": plan.plan_id})
            record = self.engine.run_plan(plan, request.policy)
            state.update(
                status=record.manifest.status.value, run_id=record.run_id, run_root=str(record.root)
            )
            self._event(
                job_id, {"type": "run_finished", "status": state["status"], "run_id": record.run_id}
            )
        except Exception as error:
            state.update(status="failed", **public_error(error)[1])
            self._event(job_id, {"type": "run_failed", **public_error(error)[1]})
        except BaseException:
            state.update(
                status="failed",
                error_type="InterruptedRun",
                error="Worker was interrupted; inspect receipts before resuming.",
            )
            raise
        finally:
            try:
                state["finished_at"] = utc_now().isoformat()
                atomic_write_json(state_path, state)
            finally:
                lock.release()

    def start_run(self, request: RunRequest) -> dict[str, Any]:
        plan = self._select_plan(request)
        engine = self.engine
        engine._preflight(plan, request.policy)
        fingerprint = engine.execution_sha256(plan, request.policy)
        job_id = f"job-{fingerprint[:16]}"
        queued = {
            "job_id": job_id,
            "plan_id": plan.plan_id,
            "status": "queued",
            "execution_sha256": fingerprint,
            "created_at": utc_now().isoformat(),
        }
        lock = self._job_lock(job_id)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            path = self.job_dir / f"{job_id}.json"
            return self._read_state(job_id) if path.is_file() else queued
        handed_off = False
        try:
            state_path = self.job_dir / f"{job_id}.json"
            if state_path.is_file():
                existing = self._read_state(job_id)
                if existing.get("execution_sha256") != fingerprint:
                    raise ResumeConflict("Job directory contains a different execution identity")
                if existing.get("status") in {"complete", "partial"}:
                    return existing
                if existing.get("status") in {"queued", "running"}:
                    self._mark_interrupted(job_id, existing)
            with self._mutex:
                if self._closing:
                    raise StoryCanvasError("Service is shutting down")
                atomic_write_json(
                    self.request_dir / f"{job_id}.json",
                    RunRequest(plan=plan, policy=request.policy),
                )
                atomic_write_json(state_path, queued)
                self._pool.submit(self._run_job, job_id, request, plan, lock)
                handed_off = True
            return queued
        finally:
            if not handed_off:
                lock.release()

    def _read_state(self, job_id: str) -> dict[str, Any]:
        path = self.job_dir / f"{job_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Unknown run job: {job_id}")
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def _mark_interrupted(self, job_id: str, state: dict[str, Any]) -> None:
        state.update(
            status="failed",
            error_type="InterruptedRun",
            error="Previous worker exited; resubmit explicitly to recover from persisted receipts.",
            finished_at=utc_now().isoformat(),
        )
        atomic_write_json(self.job_dir / f"{job_id}.json", state)
        self._event(job_id, {"type": "run_interrupted"})

    def get_run(self, job_id: str) -> dict[str, Any]:
        ensure_safe_id(job_id, label="job_id")
        state = self._read_state(job_id)
        if state.get("status") in {"queued", "running"}:
            try:
                with self._job_lock(job_id).acquire(timeout=0):
                    state = self._read_state(job_id)
                    if state.get("status") in {"queued", "running"}:
                        self._mark_interrupted(job_id, state)
            except Timeout:
                pass
        return state

    def events(self, job_id: str) -> list[dict[str, Any]]:
        self.get_run(job_id)
        return read_jsonl(self.event_dir / f"{job_id}.jsonl")


def public_error(error: Exception) -> tuple[int, dict[str, str]]:
    if isinstance(error, FileNotFoundError):
        status = 404
    elif isinstance(error, (ValueError, StoryCanvasError)):
        status = 400
    else:
        return 500, {"error": "Internal StoryCanvas error", "error_type": type(error).__name__}
    return status, {"error": safe_error(error), "error_type": type(error).__name__}
