"""Run three credential-free demos through the Profile/Plugin composition path."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.plugins import load_profile
from storycanvas_harness.schemas import ExecutionPolicy, ShotInput, StoryInput
from storycanvas_harness.utils import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
INPUT_ROOT = ROOT / "demos" / "plugin_profiles" / "inputs"
OUTPUT_ROOT = ROOT / "output" / "plugin_demos"
PROFILE_PATH = ROOT / "profiles" / "basic.json"


def _load_case(path: Path) -> tuple[ShotInput | StoryInput, ExecutionPolicy]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    input_kind = payload.get("input_kind")
    if input_kind == "shot":
        value: ShotInput | StoryInput = ShotInput.model_validate(payload["payload"])
    elif input_kind == "story":
        value = StoryInput.model_validate(payload["payload"])
    else:
        raise ValueError(f"Unsupported input_kind in {path}: {input_kind!r}")
    return value, ExecutionPolicy.model_validate(payload["policy"])


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    profile = load_profile(PROFILE_PATH)
    results: list[dict[str, Any]] = []

    for input_path in sorted(INPUT_ROOT.glob("*.json")):
        value, policy = _load_case(input_path)
        case_id = input_path.stem
        with StoryCanvas.from_profile(
            PROFILE_PATH,
            runs_dir=OUTPUT_ROOT / case_id / "runs",
        ) as canvas:
            plan = canvas.plan(value, policy)
            record = canvas.run_plan(plan, policy)

        artifact_kinds = Counter(item.kind for item in record.manifest.artifacts)
        results.append(
            {
                "case_id": case_id,
                "input": str(input_path.relative_to(ROOT)),
                "mode": policy.mode.value,
                "status": record.manifest.status.value,
                "run_id": record.run_id,
                "run_root": str(record.root.relative_to(ROOT)),
                "plan_id": plan.plan_id,
                "shots": len(plan.shots),
                "plugins_profile": profile.name,
                "profile_sha256": profile.sha256,
                "composition_sha256": record.manifest.composition_sha256,
                "workflow_sha256": record.compiled_workflow.workflow_sha256,
                "call_counts": record.manifest.call_counts,
                "artifact_kinds": dict(sorted(artifact_kinds.items())),
                "errors": record.manifest.errors,
            }
        )
        print(
            f"{case_id}: {record.manifest.status.value} · "
            f"{len(plan.shots)} shot(s) · {record.root.relative_to(ROOT)}"
        )

    report = {
        "schema_version": "storycanvas/demo-report/v1",
        "profile": str(PROFILE_PATH.relative_to(ROOT)),
        "profile_sha256": profile.sha256,
        "composition_sha256": results[0]["composition_sha256"],
        "network_calls": 0,
        "paid_calls": 0,
        "cases": results,
    }
    atomic_write_json(OUTPUT_ROOT / "demo_report.json", report)
    print(f"report: {(OUTPUT_ROOT / 'demo_report.json').relative_to(ROOT)}")
    if any(result["status"] != "complete" for result in results):
        raise SystemExit("One or more demos failed; inspect demo_report.json.")


if __name__ == "__main__":
    main()
