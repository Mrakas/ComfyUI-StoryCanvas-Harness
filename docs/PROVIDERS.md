# Provider guide

Providers implement small protocols; the harness owns planning limits, dependency order, cache identity, and audit output.

## Environment-backed defaults

| Capability | Default implementation | Environment |
|---|---|---|
| Director | OpenAI Responses structured output | `OPENAI_API_KEY`, `OPENAI_TEXT_MODEL` |
| Factual search | OpenAI Responses `web_search` | same OpenAI variables |
| Image generation/edit | OpenAI Images | `OPENAI_API_KEY`, `OPENAI_IMAGE_MODEL` |
| Visual search | Serper Images, optional | `SERPER_API_KEY` |
| Video | MiniMax-H3-compatible async HTTP | `MINIMAX_H3_API_KEY`, `MINIMAX_H3_BASE_URL`, `MINIMAX_H3_MODEL` |

## Codex app-server login mode

Install the optional SDK and select the provider explicitly:

```bash
uv sync --extra codex
export STORYCANVAS_PROVIDER_MODE=codex
export STORYCANVAS_CODEX_ENABLED=true
export STORYCANVAS_CODEX_MODEL=gpt-5.6-sol
export STORYCANVAS_CODEX_REASONING_EFFORT=medium
```

`CodexAppServerClient` launches the local Codex app-server through the official
Python SDK. It calls the account and model-capability methods, requires an
existing `chatgpt` login, verifies the requested model and reasoning effort,
and creates one ephemeral thread per operation. It never opens, copies, logs,
or serializes `auth.json`.

`CodexDirector` uses strict JSON Schema output. `CodexImageProvider` sends the
actual ordered files as `localImage` inputs and accepts only a completed native
`imageGeneration` item. Receipts retain thread, turn, item, model, effort,
actual Prompt, duration, and non-secret usage metadata. Login expiry, quota,
moderation, missing ImageGen, and transport failures stop the real run; there
is no automatic fallback to an API key or mock image.

With `STORYCANVAS_PROVIDER_MODE=mock`, the harness uses deterministic offline planning, mock search, deterministic PNGs, and a one-second ffmpeg video. Mock mode is for tests and workflow demonstrations.

If no `OPENAI_API_KEY` is present and mock mode is not selected, planning falls back to an offline preview Director while real search and image providers stay unavailable. The plan includes an explicit warning.

## OpenAI Director

`OpenAIDirector` calls `responses.parse(..., text_format=DirectorDraft)`. The system prompt tells the model to plan visual semantics rather than ComfyUI serialization. `DirectorDraft` is validated before conversion to `CanvasPlan`.

The OpenAI SDK reads credentials from the environment. The project never includes key values in request identities, receipts, workflow JSON, or error exports. OpenAI documents environment-based key setup in its [developer quickstart](https://platform.openai.com/docs/quickstart/make-your-first-api-request).

## Factual web search

`OpenAIFactSearch` uses the built-in `web_search` tool for concrete visual facts such as materials, era, architecture, and geography. It collects source URLs exposed by the response and stores the complete typed `SearchResult` in a receipt.

Factual search enriches the *text* prompt. It does not pretend that a cited page image was supplied to the image model.

## Visual search

`SerperVisualSearch` is separate from factual search. When `visual_search_query` is present, the selected result is:

1. recorded with page URL, image URL, publisher, query, and provider receipt;
2. downloaded only over public HTTPS;
3. limited to 20 MiB;
4. required to return an image content type;
5. decoded and re-encoded as a PNG;
6. added as an explicit ordered `visual_search` reference to image generation.

No search result is relabeled as an AI-generated image.

## OpenAI images

Text-only assets use the Images generation endpoint. Assets with references use image editing with the ordered reference files. The provider normalizes output to a 1344×768 black-letterboxed PNG while retaining the provider request receipt and actual Prompt.

The model is configurable because model availability changes. Check the current official [OpenAI model catalog](https://platform.openai.com/docs/models) and Images API documentation before pinning a production revision.

## MiniMax-H3-compatible video

The adapter implements:

```text
POST /v1/files/upload
POST /v2/video_generation
GET  /v2/query/video_generation/{task_id}
```

It uploads one to nine images in the supplied order, creates a 768P 16:9 10-second request, persists task state atomically, polls the same task, downloads through a temporary `.part` file, and returns a receipt.

Important recovery rules:

- Request identity includes model, Prompt, ordered reference names/SHA, resolution, duration, and ratio.
- A state file with a different request SHA raises `ResumeConflict`.
- A successful state plus output file resumes without creating a task.
- Poll failures keep the task ID and may resume safely.
- An ambiguous create response stops with `create_ambiguous`; no automatic creation retry occurs.

## Adding a provider

Implement the matching protocol from `providers/base.py`, then inject it into `StoryCanvas(...)`. A production-quality provider should include:

- deterministic request identity;
- typed receipt without secrets;
- explicit model/revision;
- bounded timeouts;
- atomic task/output persistence;
- exact reference order;
- a mock-backed unit test for success, failure, and recovery;
- no hidden retries for potentially billable creation calls.
