from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storycanvas_harness.api import create_app
from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.schemas import ExecutionMode, ExecutionPolicy
from storycanvas_harness.service import PlanRequest, RunRequest, StoryCanvasService, WorkflowRequest


def test_service_plan_compile_and_async_run(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    service = StoryCanvasService(tmp_path / "service", engine=mock_canvas)
    plan = service.create_plan(
        PlanRequest(input_kind="shot", payload={"prompt": "A fictional comet crosses the sky."})
    )
    compiled = service.compile(WorkflowRequest(plan_id=plan.plan_id))
    assert compiled["plan_id"] == plan.plan_id
    state = service.start_run(
        RunRequest(plan_id=plan.plan_id, policy=ExecutionPolicy(mode=ExecutionMode.PLAN_ONLY))
    )
    for _ in range(100):
        current = service.get_run(state["job_id"])
        if current["status"] != "queued" and current["status"] != "running":
            break
        time.sleep(0.02)
    assert current["status"] == "complete"
    assert [event["type"] for event in service.events(state["job_id"])] == [
        "run_started",
        "run_finished",
    ]


def test_fastapi_contract(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    service = StoryCanvasService(tmp_path / "service", engine=mock_canvas)
    client = TestClient(create_app(service))
    response = client.post(
        "/storycanvas/v1/plans",
        json={
            "input_kind": "story",
            "payload": {"free_text": "First shot.\nSecond shot."},
            "policy": ExecutionPolicy().model_dump(mode="json"),
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert len(plan["shots"]) == 2
    workflow = client.post(
        "/storycanvas/v1/workflows",
        json={"plan_id": plan["plan_id"], "policy": ExecutionPolicy().model_dump(mode="json")},
    )
    assert workflow.status_code == 200
    assert "definitions" in workflow.json()["workflow"]


def test_missing_plan_returns_404(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    client = TestClient(create_app(StoryCanvasService(tmp_path / "service", engine=mock_canvas)))
    response = client.get("/storycanvas/v1/plans/does-not-exist")
    assert response.status_code == 404


def test_api_rejects_path_traversal_ids(mock_canvas: StoryCanvas, tmp_path: Path) -> None:
    service = StoryCanvasService(tmp_path / "service", engine=mock_canvas)
    client = TestClient(create_app(service))
    # Starlette may reject encoded slashes at the router (404) before our stricter
    # identifier validator can return 400. Either outcome safely blocks traversal.
    assert client.get("/storycanvas/v1/plans/..%2F..%2Fsecret").status_code in {400, 404}
    assert client.get("/storycanvas/v1/runs/..%2F..%2Fsecret").status_code in {400, 404}
    with pytest.raises(ValueError):
        service.get_plan("../../secret")
    with pytest.raises(ValueError):
        service.get_run("../../secret")
