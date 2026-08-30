#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import http.server
import shutil
import subprocess  # nosec B404
import tempfile
import threading
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.sync_api import sync_playwright


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/pipeline_demo_v3.html"


def _run(argv: list[str], *, timeout: int = 600) -> None:
    result = subprocess.run(  # nosec B603
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr}"
        )


def _external_requests(requests: list[str], origin: str) -> list[str]:
    return [url for url in requests if not (url.startswith(origin) or url.startswith("data:"))]


def _check_public_text(page: Any) -> None:
    text = page.locator("body").inner_text().lower()
    forbidden = ["api_key", "thread=", "turn=", "task_id", "/users/", "provider request"]
    leaks = [token for token in forbidden if token in text]
    if leaks:
        raise RuntimeError(f"Director activity panel leaked forbidden identifiers: {leaks}")


def render(source_dir: Path, video_output: Path, poster_output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    graph_path, media_dir = source_dir / "canvas_graph.json", source_dir / "media"
    if not graph_path.is_file() or not media_dir.is_dir():
        raise ValueError("The exported StoryCanvas graph and media directory are required")

    root = Path(__file__).resolve().parents[1]
    templates = root / "storycanvas_harness" / "templates"
    video_output.parent.mkdir(parents=True, exist_ok=True)
    poster_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="storycanvas-pipeline-v3-") as temp_name:
        temp = Path(temp_name)
        site = temp / "site"
        site.mkdir()
        shutil.copy2(templates / "pipeline_demo.html", site / "pipeline_demo.html")
        shutil.copy2(templates / "pipeline_demo_v3.html", site / "pipeline_demo_v3.html")
        shutil.copy2(graph_path, site / "canvas_graph.json")
        try:
            (site / "media").symlink_to(media_dir.resolve(), target_is_directory=True)
        except OSError:
            shutil.copytree(media_dir, site / "media")

        server, url = _serve(site)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=["--autoplay-policy=no-user-gesture-required"],
                )
                origin = url.rsplit("/", 1)[0]

                poster_context = browser.new_context(
                    viewport={"width": 1920, "height": 1080}, device_scale_factor=1
                )
                poster_page = poster_context.new_page()
                poster_errors: list[str] = []
                poster_requests: list[str] = []
                poster_page.on(
                    "console",
                    lambda msg: poster_errors.append(msg.text) if msg.type == "error" else None,
                )
                poster_page.on("pageerror", lambda error: poster_errors.append(str(error)))
                poster_page.on("request", lambda request: poster_requests.append(request.url))
                poster_page.goto(f"{url}?final=1", wait_until="networkidle")
                poster_page.wait_for_function(
                    "window.pipelineDemoV3.ready && window.pipelineDemoV3.done", timeout=35_000
                )
                poster_page.wait_for_timeout(900)
                if poster_page.locator(".message.visible").count() != 10:
                    raise RuntimeError(
                        "Final Director panel must display exactly ten activity messages"
                    )
                shot2 = poster_page.locator('.message[data-index="5"]')
                shot2_text = shot2.inner_text().lower()
                if "2 ordered refs" not in shot2_text or "2 attempts" not in shot2_text:
                    raise RuntimeError("Shot 02 receipt summary does not match the audited graph")
                _check_public_text(poster_page)
                poster_page.screenshot(path=str(temp / "poster.png"), full_page=False)
                if poster_errors:
                    raise RuntimeError(f"Director demo console errors: {poster_errors}")
                if external := _external_requests(poster_requests, origin):
                    raise RuntimeError(f"Director demo made non-local requests: {external}")
                poster_context.close()

                video_context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    device_scale_factor=1,
                    record_video_dir=str(temp / "recorded"),
                    record_video_size={"width": 1920, "height": 1080},
                )
                page = video_context.new_page()
                errors: list[str] = []
                requests: list[str] = []
                page.on(
                    "console", lambda msg: errors.append(msg.text) if msg.type == "error" else None
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.goto(f"{url}?demo=1", wait_until="networkidle")
                page.wait_for_function("window.pipelineDemoV3.done", timeout=40_000)
                page.wait_for_timeout(300)
                if page.locator(".message.visible").count() != 10:
                    raise RuntimeError("Recorded Director panel did not complete all ten messages")
                _check_public_text(page)
                raw_video = page.video
                video_context.close()
                browser.close()
                if errors:
                    raise RuntimeError(f"Director demo console errors: {errors}")
                if external := _external_requests(requests, origin):
                    raise RuntimeError(f"Director demo made non-local requests: {external}")
                raw_path = Path(raw_video.path())

            with Image.open(temp / "poster.png") as image:
                image.save(poster_output, "WEBP", quality=88, method=6)
            _run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    "0.12",
                    "-i",
                    str(raw_path),
                    "-t",
                    "28.0",
                    "-vf",
                    "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                    "-r",
                    "24",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(video_output),
                ],
                timeout=1200,
            )
            _run([ffmpeg, "-v", "error", "-i", str(video_output), "-f", "null", "-"])
        finally:
            server.shutdown()
            server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render StoryCanvas with a receipts-backed Director activity panel."
    )
    parser.add_argument("--source-dir", type=Path, default=Path("examples/moon_garden_canvas"))
    parser.add_argument(
        "--video-output", type=Path, default=Path("assets/demo/storycanvas-pipeline-demo-v3.mp4")
    )
    parser.add_argument(
        "--poster-output",
        type=Path,
        default=Path("assets/demo/storycanvas-pipeline-demo-v3-poster.webp"),
    )
    args = parser.parse_args()
    render(args.source_dir.resolve(), args.video_output.resolve(), args.poster_output.resolve())


if __name__ == "__main__":
    main()
