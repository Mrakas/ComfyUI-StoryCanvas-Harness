# API

Start the standalone service:

```bash
uv run storycanvas serve --host 127.0.0.1 --port 8189
```

The same path namespace is registered inside ComfyUI.

## Health

```http
GET /health
```

The standalone FastAPI app exposes `/health`. ComfyUI owns its own server health routes.

## Create a plan

```http
POST /storycanvas/v1/plans
Content-Type: application/json
```

```json
{
  "input_kind": "shot",
  "payload": {
    "shot_id": "paper-fox",
    "prompt": "A fictional paper fox follows three blue lanterns."
  },
  "policy": {
    "mode": "plan_only",
    "allow_paid_video": false,
    "max_shots": 12,
    "max_search_calls": 8,
    "max_image_calls": 16,
    "max_video_calls": 0,
    "max_concurrency": 4,
    "require_preview": true
  }
}
```

The response is a complete `CanvasPlan`. It includes exact call estimates and warnings but performs no media call in `plan_only` mode.

## Retrieve a plan

```http
GET /storycanvas/v1/plans/{plan_id}
```

Identifiers are restricted to filesystem-safe ASCII characters; path traversal is rejected.

## Compile a workflow

```http
POST /storycanvas/v1/workflows
```

Using a stored plan:

```json
{
  "plan_id": "plan-0123456789abcdef",
  "policy": {
    "mode": "plan_only",
    "allow_paid_video": false,
    "max_shots": 12,
    "max_search_calls": 8,
    "max_image_calls": 16,
    "max_video_calls": 0,
    "max_concurrency": 4,
    "require_preview": true
  }
}
```

You may send `plan` instead of `plan_id`. The response contains `workflow`, `api_workflow`, `node_index`, warnings, and `workflow_sha256`.

## Start a run

```http
POST /storycanvas/v1/runs
```

```json
{
  "plan_id": "plan-0123456789abcdef",
  "policy": {
    "mode": "assets",
    "allow_paid_video": false,
    "max_shots": 3,
    "max_search_calls": 4,
    "max_image_calls": 8,
    "max_video_calls": 0,
    "max_concurrency": 4,
    "require_preview": true
  }
}
```

The server returns `202` with a stable job ID derived from plan ID and policy. Repeating the same request returns the existing queued/running/complete/partial state instead of starting a duplicate.

## Poll state and events

```http
GET /storycanvas/v1/runs/{job_id}
GET /storycanvas/v1/runs/{job_id}/events
```

Events are append-only JSON objects for lifecycle status, not token-by-token model output.

## Error shape

```json
{
  "detail": {
    "error": "human-readable message",
    "error_type": "PolicyViolation"
  }
}
```

Validation and policy failures return `400`, missing plans/jobs return `404`, and unexpected internal errors return a redacted `500`.

## Standalone Canvas export

The read-only Viewer export is available directly from Python:

```python
from storycanvas_harness import export_story_canvas

report = export_story_canvas("runs/run-id", "public/canvas")
```

or through the CLI:

```bash
uv run storycanvas canvas-export \
  --run-dir runs/run-id --output-dir public/canvas
```

Both surfaces validate `canvas_plan.json`, `run_manifest.json`, declared media paths, SHA-256,
image decoding, and full video decoding. They emit the `storycanvas/canvas/v1` graph, a
self-contained Viewer, content-addressed media, and a machine-readable export report. Export does
not call a model or provider.
