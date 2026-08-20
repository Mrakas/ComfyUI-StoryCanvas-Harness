"""Build deterministic, fictional, credential-free StoryCanvas examples."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storycanvas_harness.engine import StoryCanvas
from storycanvas_harness.providers.director import DeterministicDirector
from storycanvas_harness.providers.image import MockImageProvider
from storycanvas_harness.providers.search import MockFactSearch, MockVisualSearch
from storycanvas_harness.schemas import ExecutionMode, ExecutionPolicy, ShotInput, StoryInput
from storycanvas_harness.utils import atomic_write_json, atomic_write_text

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = "2026-01-01T00:00:00Z"


EXAMPLES: dict[str, ShotInput | StoryInput] = {
    "single_shot": ShotInput(
        shot_id="clockmaker-shot",
        title="The Clockwork Bird",
        prompt=(
            "In a fictional amber-lit workshop, an elderly clockmaker tightens one brass screw "
            "on a palm-sized mechanical bird. The bird wakes, tests both wings, and lands beside "
            "a blue enamel teacup."
        ),
    ),
    "three_shot_story": StoryInput(
        story_id="moon-garden",
        title="The Moon Garden",
        shots=[
            ShotInput(
                shot_id="garden-01",
                title="Seed",
                prompt=(
                    "A fictional young botanist in a moss-green coat plants a silver seed in a "
                    "moonlit glasshouse; a red field notebook rests by her left hand."
                ),
            ),
            ShotInput(
                shot_id="garden-02",
                title="Bloom",
                prompt=(
                    "In the same glasshouse, the same botanist opens the red notebook as the "
                    "silver seed grows into a luminous blue vine around the same clay pot."
                ),
            ),
            ShotInput(
                shot_id="garden-03",
                title="Release",
                prompt=(
                    "The same botanist raises the same clay pot; the blue vine releases three "
                    "glowing moths that circle her while the open red notebook remains on the bench."
                ),
            ),
        ],
    ),
}


def _portable(value: Any, old_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                FIXED_TIME
                if key in {"created_at", "started_at", "finished_at"}
                else _portable(item, old_root)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_portable(item, old_root) for item in value]
    if isinstance(value, str):
        prefix = f"{old_root}{Path('/')}"
        if value == str(old_root):
            return "."
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _normalize_json(path: Path, old_root: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    atomic_write_json(path, _portable(payload, old_root))


def _normalize_jsonl(path: Path, old_root: Path) -> None:
    rows = [
        _portable(json.loads(line), old_root)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
    )


def _write_manifest(root: Path) -> None:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "MANIFEST.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(root).as_posix()}\n")
    atomic_write_text(root / "MANIFEST.sha256", "".join(rows))


def build_example(name: str, value: ShotInput | StoryInput) -> None:
    target = ROOT / "examples" / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shot_count = 1 if isinstance(value, ShotInput) else len(value.shots)
    policy = ExecutionPolicy(
        mode=ExecutionMode.ASSETS,
        max_shots=shot_count,
        max_image_calls=shot_count + 1,
        max_video_calls=0,
        max_concurrency=min(4, shot_count + 1),
    )
    with tempfile.TemporaryDirectory(prefix=f"storycanvas-{name}-") as temporary:
        canvas = StoryCanvas(
            runs_dir=Path(temporary) / "runs",
            director=DeterministicDirector(),
            fact_search=MockFactSearch(),
            visual_search=MockVisualSearch(),
            image_provider=MockImageProvider(),
        )
        plan = canvas.plan(value, policy)
        plan.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        record = canvas.run_plan(plan, policy)
        old_root = record.root
        for path in old_root.rglob("*"):
            relative = path.relative_to(old_root)
            destination = target / relative
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)

        input_kind = "shot" if isinstance(value, ShotInput) else "story"
        atomic_write_json(
            target / "input.json",
            {
                "input_kind": input_kind,
                "payload": value.model_dump(mode="json", exclude_none=True),
                "policy": policy.model_dump(mode="json", exclude_none=True),
            },
        )
        for path in target.rglob("*.json"):
            _normalize_json(path, old_root)
        for path in target.rglob("*.jsonl"):
            _normalize_jsonl(path, old_root)
        audit = target / "audit.html"
        atomic_write_text(audit, audit.read_text(encoding="utf-8").replace(str(old_root), "."))
        _write_manifest(target)


def main() -> None:
    for name, value in EXAMPLES.items():
        build_example(name, value)
        print(f"built examples/{name}")


if __name__ == "__main__":
    main()
