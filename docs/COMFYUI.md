# ComfyUI integration

StoryCanvas is both a ComfyUI custom-node package and a workflow compiler.

## Requirements

- A working ComfyUI installation.
- ComfyUI frontend 1.24.3 or newer for native shot subgraphs.
- Python 3.10–3.13.
- `ffmpeg` and `ffprobe` for video validation and story concatenation.
- Provider credentials only for the capabilities you explicitly enable.

Release `0.1.0` is browser-validated against ComfyUI `0.33.0` with frontend
`1.48.7`; CI separately covers Python 3.10 and 3.12. Newer ComfyUI releases may
change frontend extension or subgraph serialization details, so report the exact
backend/frontend versions with compatibility bugs.

ComfyUI documents the client/server custom-node model in its official [custom-node overview](https://docs.comfy.org/custom-nodes/overview), frontend extension entry points in [Javascript Extensions](https://docs.comfy.org/custom-nodes/js/javascript_overview), and native subgraphs in the [Subgraph guide](https://docs.comfy.org/interface/features/subgraph).

## Install

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/Mrakas/ComfyUI-StoryCanvas-Harness.git
cd ComfyUI-StoryCanvas-Harness
/path/to/ComfyUI/python -m pip install -r requirements.txt
```

Use the same Python environment that launches ComfyUI. Restart the server and confirm that thirteen `StoryCanvas` node classes appear without import errors.

For a pinned local macOS installation beside this repository checkout:

```bash
cd /path/to/ComfyUI-StoryCanvas-Harness
./scripts/install_local_comfyui_macos.sh
./scripts/start_local_comfyui.sh
```

The installer pins ComfyUI `v0.33.0`, creates a Python 3.12 `uv` environment,
and links this checkout as a custom node; it does not download model weights.
The start script listens only on `127.0.0.1:8188`, enables Codex login mode without
an OpenAI API key, and leaves MiniMax credentials entirely to the caller's
runtime environment.

## Builder flow

1. Open **Extensions → StoryCanvas → Build StoryCanvas…**, or right-click the canvas.
2. Choose single Shot or Story, and free text or structured JSON.
3. Keep `plan_only` for the first preview.
4. Click **Build preview**.
5. Review shot count, predicted planning/search/image/video calls, warnings, and paid-video lock state.
6. Click **Apply to new workflow**.
7. Inspect or edit the resulting graph.
8. Queue manually only when ready.

The extension calls StoryCanvas routes hosted by the same ComfyUI server. `app.loadGraphData(..., filename)` is used to create a separate temporary workflow tab, leaving the previous canvas available.

## Nodes

| Node | Role |
|---|---|
| `StoryCanvas Input` | Validates raw Shot/Story JSON |
| `Execution Policy` | Validates runtime limits and spend gates |
| `StoryCanvas Director` | Loads a precomputed plan or invokes a configured Director |
| `Shared Visual Asset` | Resolves/generates reusable style, location, character, or prop assets |
| `Reference Asset` | Resolves direct references or generates the final Canvas keyframe |
| `Reference Pack` | Preserves exact reference order |
| `H3 Prompt Compiler` | Binds `Image 1…N` roles to the six-part prompt |
| `MiniMax H3 API` | Runs the gated, resumable video provider |
| `Story Assemble` | Concatenates ready per-shot videos |
| `Run Manifest` | Writes manifest, JSONL prompt audit, and HTML audit |
| `Text Preview (Read-only)` | Displays existing Story, Prompt, receipt, and continuity text through `ui.text` |
| `Image Preview (Read-only)` | Displays existing references, Canvas keyframes, and midframes through `ui.images` |
| `Video Preview (Read-only)` | Displays existing MP4 files through ComfyUI animated-media UI |

## Read-only Run review App

To review an already-completed StoryCanvas Run without exposing any generation
controls or making provider calls:

```bash
uv run storycanvas comfy-review \
  --run-dir /path/to/completed/run-id \
  --comfy-root /path/to/ComfyUI
```

The importer validates the Run manifest, Prompt records, receipts, media SHA-256,
image decoding, `ffprobe`, and complete video decoding. It then hard-links media
under `ComfyUI/input/storycanvas/<run_id>/` (copying only when a hard link is not
possible), writes `review_workflow.json` beside the Run, and saves a `.app.json`
workflow under `ComfyUI/user/default/workflows/`.

The saved workflow defaults to a media-first Graph Mode, where the three
read-only preview node types expose images, videos, Prompts, and the Visual Bible
as a dependency DAG. **Run** reads local files and cannot call the Director,
ImageGen, MiniMax, or another network provider. App Mode remains available for a
gallery-style view.

## Subgraph layout

The root graph contains Input, Policy, Director, shared assets, one subgraph instance per shot, Story Assemble, and Manifest. Double-click a shot subgraph to inspect:

```text
direct/user/shared references
          ↓
ordered Canvas pack
          ↓
final Canvas keyframe
          ↓
Canvas-first H3 reference pack
          ↓
six-part H3 prompt
          ↓
gated video node
```

The compiler includes subgraph definitions inside the workflow. This is different from publishing a reusable global subgraph blueprint, though ComfyUI also supports blueprints through a `subgraphs/` directory.

## Load the examples

Drag either file into ComfyUI or use **Workflow → Open**:

- `examples/single_shot/workflow.json`
- `examples/three_shot_story/workflow.json`

They are precomputed `assets`-mode graphs with mock output artifacts checked into the repository. Change the Policy deliberately before executing with real providers.

## API execution

ComfyUI API format maps node IDs to `class_type` and `inputs`. StoryCanvas emits that flat representation as `workflow_api.json`. Official ComfyUI documentation describes the API-format graph and asynchronous job model in the [Cloud API overview](https://docs.comfy.org/development/cloud/overview); local ComfyUI uses the analogous prompt queue.

Do not submit the editable UI workflow directly to `/prompt`; use the API-format file.

## Troubleshooting

- **No StoryCanvas menu:** refresh the browser after restarting ComfyUI; inspect server logs for custom-node import errors.
- **Subgraphs appear missing:** update the ComfyUI frontend to at least 1.24.3.
- **Plan preview works but assets fail:** configure the required provider key and increase the matching policy limit.
- **Video is locked:** set both `mode=full` and `allow_paid_video=true`, with `max_video_calls > 0`.
- **Ambiguous task creation:** inspect the saved `*.video-task.json`; reconcile it with the provider before any retry.
- **Manifest is partial:** inspect receipts and missing dependencies. A downstream shot is not marked ready when its input asset failed.
