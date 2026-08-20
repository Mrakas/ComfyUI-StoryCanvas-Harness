# Limitations

## Alpha compatibility

- The UI compiler targets ComfyUI workflow version `0.4` and native frontend subgraphs. Subgraph serialization is evolving; test pinned ComfyUI/frontend revisions before a long run.
- v0.1 supports at most 24 shots in one workflow.
- It supports at most five ordered references for each Canvas keyframe and nine for H3 video.
- The standalone service uses in-process background threads. For multi-user deployment, use a durable external queue and authentication layer.

## Provider behavior

- OpenAI, Serper, and MiniMax-compatible interfaces are optional external services and may change independently.
- The default model names are configuration defaults, not promises of permanent availability.
- Search results can disappear or change. Receipts retain URLs and hashes, but this project does not archive third-party webpages.
- Image providers can reject prompts or references. Rejections are reported; the harness does not disguise searched images as generated substitutes.
- A provider may complete a paid task after an ambiguous client response. Manual reconciliation is required before retrying.

## Continuity

- Explicit reference graphs improve controllability but do not guarantee consistent characters, clothing, background, props, motion, or physics.
- A previous keyframe is used only when explicitly planned. The quality of that decision depends on the Director and user review.
- Story concatenation is a simple ordered media concat; transitions, color matching, audio mixing, and editorial pacing are future work.

## Examples

- Checked-in PNGs come from `MockImageProvider`. They validate file handling, prompts, SHA, workflow structure, and audit behavior—not image quality.
- No real paid video is checked into Git. Release assets may be added separately after license and size review.
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
