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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/index.html"


def _run(argv: list[str], *, timeout: int = 600) -> None:
    result = subprocess.run(  # nosec B603
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr}"
        )


def render(canvas_dir: Path, video_output: Path, poster_output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    if not (canvas_dir / "index.html").is_file():
        raise ValueError(f"Missing standalone Viewer: {canvas_dir / 'index.html'}")
    video_output.parent.mkdir(parents=True, exist_ok=True)
    poster_output.parent.mkdir(parents=True, exist_ok=True)
    server, url = _serve(canvas_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="storycanvas-demo-") as temp_name:
            temp = Path(temp_name)
            screenshot = temp / "poster.png"
            recorded_dir = temp / "recorded"
            with sync_playwright() as playwright:
                launch_args = ["--autoplay-policy=no-user-gesture-required"]
                browser = playwright.chromium.launch(
                    channel="chrome", headless=True, args=launch_args
                )
                poster_context = browser.new_context(
                    viewport={"width": 1920, "height": 1080}, device_scale_factor=1
                )
                poster_page = poster_context.new_page()
                browser_errors: list[str] = []
                requests: list[str] = []
                poster_page.on(
                    "console",
                    lambda message: (
                        browser_errors.append(message.text) if message.type == "error" else None
                    ),
                )
                poster_page.on("pageerror", lambda error: browser_errors.append(str(error)))
                poster_page.on("request", lambda request: requests.append(request.url))
                poster_page.goto(f"{url}?final=1", wait_until="networkidle")
                poster_page.wait_for_function(
                    "[...document.images].every(image => image.complete)", timeout=30_000
                )
                poster_page.evaluate("window.storyCanvasDemo.setStage(0)")
                if poster_page.locator(".node.visible").count() != 0:
                    raise RuntimeError("Reset state unexpectedly exposes Canvas nodes")
                for expected_stage in range(1, 7):
                    poster_page.get_by_role("button", name="Next").click()
                    actual_stage = poster_page.evaluate("window.storyCanvasDemo.stage")
                    if actual_stage != expected_stage:
                        raise RuntimeError(
                            f"Next control produced stage {actual_stage}, expected {expected_stage}"
                        )
                expected_nodes = poster_page.locator(".node").count()
                if poster_page.locator(".node.visible").count() != expected_nodes:
                    raise RuntimeError("Final stage does not expose every Canvas node")
                if poster_page.locator(".node.video video").count() != 3:
                    raise RuntimeError("Expected exactly three playable Shot videos")
                poster_page.locator(".node.image").first.click()
                if not poster_page.locator("#details").evaluate(
                    "element => element.classList.contains('open')"
                ):
                    raise RuntimeError("Node details pane did not open")
                poster_page.get_by_role("button", name="Close").click()
                poster_page.evaluate("window.storyCanvasDemo.setStage(6)")
                poster_page.wait_for_timeout(900)
                poster_page.screenshot(path=str(screenshot), full_page=False)
                if browser_errors:
                    raise RuntimeError(f"Viewer console errors: {browser_errors}")
                if any(
                    not request.startswith(url.split("/index.html", 1)[0]) for request in requests
                ):
                    raise RuntimeError(f"Viewer made a non-local request: {requests}")
                poster_context.close()

                video_context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    device_scale_factor=1,
                    record_video_dir=str(recorded_dir),
                    record_video_size={"width": 1920, "height": 1080},
                )
                page = video_context.new_page()
                errors: list[str] = []
                page.on(
                    "console",
                    lambda message: (
                        errors.append(message.text) if message.type == "error" else None
                    ),
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{url}?demo=1", wait_until="networkidle")
                page.wait_for_function(
                    "window.storyCanvasDemo && !window.storyCanvasDemo.playing && "
                    "window.storyCanvasDemo.stage === 6",
                    timeout=35_000,
                )
                page.wait_for_timeout(500)
                raw_video = page.video
                video_context.close()
                browser.close()
                if errors:
                    raise RuntimeError(f"Viewer console errors: {errors}")
                raw_path = Path(raw_video.path())

            with Image.open(screenshot) as image:
                image.save(poster_output, "WEBP", quality=88, method=6)

            _run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    "0.25",
                    "-i",
                    str(raw_path),
                    "-t",
                    "24.0",
                    "-vf",
                    "scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                    "-r",
                    "24",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "23",
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
        description="Record the deterministic StoryCanvas Viewer as a README demo."
    )
    parser.add_argument("--canvas-dir", type=Path, default=Path("examples/moon_garden_canvas"))
    parser.add_argument(
        "--video-output", type=Path, default=Path("assets/demo/storycanvas-canvas-demo.mp4")
    )
    parser.add_argument(
        "--poster-output",
        type=Path,
        default=Path("assets/demo/storycanvas-canvas-demo-poster.webp"),
    )
    args = parser.parse_args()
    render(args.canvas_dir.resolve(), args.video_output.resolve(), args.poster_output.resolve())


if __name__ == "__main__":
    main()
