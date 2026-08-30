# Profile and Plugin demos

These cases exercise the same StoryCanvas engine through the declarative
`profiles/basic.json` composition rather than direct provider construction.
They are fictional, deterministic, credential-free, and make no network calls.

| Case | Mode | Purpose |
|---|---|---|
| `01_plan_only` | `plan_only` | Typed plan and ComfyUI graph, no media |
| `02_continuity_assets` | `assets` | Shared Style Bible plus a two-shot previous-frame dependency |
| `03_full_mock_story` | `full` | Three keyframes, three local mock videos, receipts, and audit output |

Run all three:

```bash
uv run python scripts/run_plugin_demos.py
```

Generated runs and `demo_report.json` are written to `output/plugin_demos/`,
which is intentionally excluded from Git. The checked-in inputs remain stable
conformance fixtures; generated media remains local.
