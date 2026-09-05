# Limitations

## Alpha compatibility

- The UI compiler targets ComfyUI workflow version `0.4` and native frontend subgraphs. Subgraph serialization is evolving; test pinned ComfyUI/frontend revisions before a long run.
- v0.1 supports at most 24 shots in one workflow.
- The built-in ComfyUI compiler supports independent shared assets and ordered shot references. Shared assets with their own dependencies/references, or dependencies without reference edges, are rejected before execution; broader DAG layouts require a custom `canvas.render` plugin.
- It supports at most five ordered references for each Canvas keyframe and nine for H3 video.
- The standalone service uses four in-process worker threads, persisted requests, and local filesystem locks. Orphaned jobs are reported as interrupted and require explicit resubmission. It does not automatically restart work or provide a distributed queue; multi-user deployments need external scheduling and authentication.

## Provider behavior

- OpenAI, Serper, and MiniMax-compatible interfaces are optional external services and may change independently.
- The default model names are configuration defaults, not promises of permanent availability.
- Search results can disappear or change. Receipts retain URLs and hashes, but this project does not archive third-party webpages.
- Image providers can reject prompts or references. Rejections are reported; the harness does not disguise searched images as generated substitutes.
- A provider may complete a paid task after an ambiguous client response. Manual reconciliation is required before retrying.

## Execution scope

- `plan_only` does not run media or evaluation plugins. A configured Director can still make a planning request.
- `doctor` inspects local configuration and installed entry-point metadata. It does not authenticate, contact endpoints, or import/start third-party plugins. Runtime validation may still fail.
- Budgets are checked against the validated graph before execution. Call counts record attempts, successes, and cache reuse; they are not provider invoices.
- New execution fingerprints deliberately create separate Run directories from historical IDs. Old manifests and validated artifacts remain readable.

## Continuity

- Explicit reference graphs improve controllability but do not guarantee consistent characters, clothing, background, props, motion, or physics.
- A previous keyframe is used only when explicitly planned. The quality of that decision depends on the Director and user review.
- Story concatenation is a simple ordered media concat; transitions, color matching, audio mixing, and editorial pacing are future work.

## Examples

- `single_shot` and `three_shot_story` contain Mock images for file handling, prompt, SHA, workflow, and audit checks. The packaged `demo` generates local Mock media and does not test real generation quality.
- `moon_garden_canvas` contains sanitized real images and MiniMax-H3 videos, with provenance and a CC BY 4.0 media license. These published assets are separate from the credential-free test suite.
- The repository intentionally contains no private benchmark inputs, benchmark outputs, model weights, internal cluster scripts, or credentials.

## Security

- The local REST service has no built-in authentication; it binds to loopback by default.
- DNS validation reduces visual-search SSRF risk but cannot fully prevent DNS rebinding.
- Pillow and ffmpeg process untrusted media; keep them patched and consider container isolation.
- Installing any ComfyUI custom node executes Python in the ComfyUI process. Review source before installation.

## Non-goals for v0.1

- Training or fine-tuning a generation model.
- Reproducing a particular paper or benchmark score.
- Automatic “best frame” selection or hidden reranking.
- A cloud billing service.
- A replacement for ComfyUI’s native editor, queue, or model ecosystem.
