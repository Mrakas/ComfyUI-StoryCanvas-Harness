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

The standalone FastAPI app exposes `/health` without creating runtime directories or initializing providers. It reports process health, not credential validity or provider readiness. ComfyUI owns its own server health routes.

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

The response is a complete `CanvasPlan`. It includes graph-derived call estimates and warnings but performs no media call in `plan_only` mode. Planning itself may contact the configured Director. Execution revalidates the graph and computes its own required calls; caller-supplied estimates cannot override budgets.

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

Send exactly one of `plan` or `plan_id`. Supplying both, neither, or an invalid graph returns `400`. The response contains `workflow`, `api_workflow`, `node_index`, warnings, and `workflow_sha256`.

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

The server returns `202` with a stable job ID derived from the full semantic plan, policy,
Profile composition, and provider configuration. Editing a plan while retaining its `plan_id`
creates a different job. Creation timestamps and ephemeral provider trace IDs do not invalidate
an otherwise identical execution.

Repeating the same request returns the existing queued/running/complete/partial state. File locks
prevent duplicate workers across service instances sharing the same local storage. Each service
has at most four worker threads; full requests are persisted under `requests/`. If a process
exits while a job is queued/running, the next read marks the orphaned job `failed` with
`InterruptedRun`. Explicit resubmission recovers from the persisted receipts; ambiguous paid
creation still requires reconciliation. This is a local service, not a distributed queue.

## Poll state and events

```http
GET /storycanvas/v1/runs/{job_id}
GET /storycanvas/v1/runs/{job_id}/events
```

Events are append-only JSON objects for lifecycle status, not token-by-token model output. An unknown job returns `404` for both status and events. An incomplete final JSONL row from an interrupted write is ignored on read and preserved separately before the next append.

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

## Run identity and accounting

New v1 manifests add optional `plan_sha256` and `execution_sha256`; existing manifests remain
readable. New Run IDs use the execution fingerprint, so old caches are not automatically reused
under a different identity. Export and video continuation accept legacy relative paths and
relocated Run directories, with all declared files confined to the Run root and verified by SHA.

`planning` counts the Director invocation performed by SDK/CLI `run`; it is zero for
`run_plan`, where planning happened separately. Local mock planning also counts as an invocation.
`image_generation` counts image generation attempts, including failures/retries;
`image_successes` counts successful new images and `image_cache_hits` counts verified reuse.
`video_generation` counts newly created video tasks, `video_attempts` counts provider invocations,
and `video_cache_hits` counts reuse/resume without task creation. These are execution counters,
not a billing ledger. Providers may have their own usage accounting.

CLI `run --json` and `complete-videos --json` return status, Run paths, and errors. A `partial`
or `failed` Run exits with code `1`; automation must not treat the existence of a manifest as
successful completion.
