from __future__ import annotations

import html
from pathlib import Path

from .schemas import CanvasPlan, RunManifest
from .utils import atomic_write_text


def _media_uri(path: str | None, destination: Path) -> str | None:
    if not path:
        return None
    try:
        relative = Path(path).expanduser().resolve().relative_to(destination.parent.resolve())
    except (OSError, ValueError):
        return None
    return html.escape(relative.as_posix(), quote=True)


def _receipt_summary(artifact: object | None) -> str:
    receipt = getattr(artifact, "receipt", None)
    if receipt is None:
        return "pending"
    request_id = receipt.provider_request_id or receipt.task_id or "no provider id"
    return html.escape(f"{receipt.provider} · {receipt.model} · {request_id}")


def render_audit(plan: CanvasPlan, manifest: RunManifest, destination: Path) -> Path:
    artifacts = {artifact.artifact_id: artifact for artifact in manifest.artifacts}
    shared_cards = []
    for asset in plan.shared_assets:
        artifact = artifacts.get(asset.asset_id)
        media_uri = _media_uri(artifact.path if artifact else None, destination)
        shared_cards.append(
            f"""
            <article class="shared">
              <header><span>VB</span><div><h2>{html.escape(asset.role)}</h2><code>{html.escape(asset.asset_id)}</code></div></header>
              <section><h3>Actual successful prompt</h3><pre>{html.escape(artifact.prompt if artifact and artifact.prompt else asset.actual_prompt)}</pre></section>
              <section><h3>Generated visual</h3>{f'<img loading="lazy" src="{media_uri}" alt="{html.escape(asset.asset_id)}">' if media_uri else "<p>pending or unavailable outside this run root</p>"}</section>
              <section><h3>Receipt</h3><p>{_receipt_summary(artifact)}</p><code>{html.escape(artifact.sha256 if artifact else "pending SHA")}</code></section>
            </article>
            """.strip()
        )
    shot_cards = []
    for shot in plan.shots:
        keyframe = artifacts.get(shot.keyframe_asset_id)
        video = artifacts.get(f"{shot.shot_id}-video")
        keyframe_uri = _media_uri(keyframe.path if keyframe else None, destination)
        video_uri = _media_uri(video.path if video else None, destination)
        actual_references = keyframe.ordered_references if keyframe else shot.references
        reference_rows = "".join(
            f"<li><b>{reference.order}. {html.escape(reference.role)}</b> "
            f"<code>{html.escape(reference.provenance.value)}</code> "
            f"<span>{html.escape(reference.sha256 or 'pending SHA')}</span></li>"
            for reference in actual_references
        )
        actual_prompt = keyframe.prompt if keyframe and keyframe.prompt else shot.image_prompt
        shot_cards.append(
            f"""
            <article>
              <header><span>{shot.order:02d}</span><div><h2>{html.escape(shot.title or shot.shot_id)}</h2><code>{html.escape(shot.shot_id)}</code></div></header>
              <section><h3>Original shot</h3><p>{html.escape(shot.original_prompt)}</p></section>
              <section><h3>Actual successful Canvas prompt</h3><pre>{html.escape(actual_prompt)}</pre></section>
              <section><h3>Ordered references</h3><ol>{reference_rows or "<li>Text-only keyframe</li>"}</ol></section>
              <section><h3>Final Canvas</h3>{f'<img loading="lazy" src="{keyframe_uri}" alt="{html.escape(shot.shot_id)} Canvas">' if keyframe_uri else "<p>pending or unavailable outside this run root</p>"}<p>{_receipt_summary(keyframe)}</p><code>{html.escape(keyframe.sha256 if keyframe else "pending SHA")}</code></section>
              <section><h3>MiniMax-H3 prompt</h3><pre>{html.escape(shot.h3_prompt)}</pre></section>
              <section><h3>Video</h3>{f'<video controls preload="metadata" src="{video_uri}"></video>' if video_uri else "<p>locked or pending</p>"}<p>{_receipt_summary(video)}</p><code>{html.escape(video.sha256 if video else "pending SHA")}</code></section>
            </article>
            """.strip()
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(plan.title)} · StoryCanvas Audit</title>
<style>
:root{{--bg:#f1f2ed;--paper:#fff;--ink:#141815;--line:#c9cdc6;--accent:#5d3fd3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 Inter,system-ui,sans-serif}}
main{{max-width:1280px;margin:auto;padding:42px}}.hero{{border-bottom:3px solid var(--ink);padding-bottom:24px;margin-bottom:30px}}
h1{{font-size:clamp(40px,7vw,84px);line-height:.92;margin:.2em 0}}.meta{{display:flex;gap:20px;flex-wrap:wrap;font-family:ui-monospace,monospace}}
article{{background:var(--paper);border:1px solid var(--line);margin:24px 0}}article header{{display:flex;gap:16px;align-items:center;padding:18px;border-bottom:1px solid var(--line)}}
article header>span{{font:30px ui-monospace,monospace;color:var(--accent)}}h2{{margin:0}}section{{padding:14px 20px;border-bottom:1px solid var(--line)}}h3{{font-size:12px;text-transform:uppercase;letter-spacing:.12em}}
pre{{white-space:pre-wrap;font:13px/1.65 ui-monospace,monospace;background:#f6f6f3;padding:14px}}code{{font-family:ui-monospace,monospace;overflow-wrap:anywhere}}ol{{padding-left:24px}}
img,video{{display:block;width:100%;max-height:720px;object-fit:contain;background:#090b0a;border:1px solid var(--line)}}.shared header>span{{font-size:18px}}
@media(max-width:700px){{main{{padding:18px}}}}
</style></head><body><main><section class="hero"><p>STORYCANVAS HARNESS · AUDIT EXPORT</p><h1>{html.escape(plan.title)}</h1>
<div class="meta"><span>{len(plan.shots)} shots</span><span>{html.escape(manifest.status.value)}</span><span>{html.escape(manifest.run_id)}</span></div></section>
<h2>Visual Bible</h2>{"".join(shared_cards)}<h2>Shots</h2>{"".join(shot_cards)}</main></body></html>"""
    atomic_write_text(destination, document)
    return destination
