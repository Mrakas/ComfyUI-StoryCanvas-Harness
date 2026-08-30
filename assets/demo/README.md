# StoryCanvas dynamic demo

`storycanvas-canvas-demo.mp4` is a deterministic 1920×1080, 24 fps recording of the same
standalone Viewer checked into `examples/moon_garden_canvas/`. It was rendered locally with
Playwright/Chrome and encoded as H.264/yuv420p with ffmpeg; it does not contain a second simulated
workflow or any new model/API output.

The MP4 and poster use the Moon Garden example's
[CC BY 4.0 media license](../../examples/moon_garden_canvas/MEDIA_LICENSE.md). Rebuild them with:

```bash
uv sync --extra demo
uv run python scripts/render_canvas_demo.py
```

`storycanvas-pipeline-demo-v2.mp4` is a second, presentation-first rendering of the same audited
run. It emphasizes the legible sequence `Story → Visual Bible → Keyframes → Clips → Story Cut`
instead of showing the complete engineering graph at once. It is also local-only and introduces no
new generated media. Rebuild it with:

```bash
uv run python scripts/render_pipeline_demo.py
```

`storycanvas-pipeline-demo-v3.mp4` keeps that presentation-first Canvas on the left and adds a
receipts-backed Director Agent activity panel on the right. The panel contains public decision
summaries and artifact events, not hidden chain-of-thought. Rebuild it with:

```bash
uv run python scripts/render_pipeline_demo_v3.py
```
