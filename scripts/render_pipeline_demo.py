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
    return server, f"http://{host}:{port}/pipeline_demo.html"


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


def _is_local(request_url: str, origin: str) -> bool:
    return request_url.startswith(origin) or request_url.startswith("data:")


def render(source_dir: Path, video_output: Path, poster_output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    graph_path = source_dir / "canvas_graph.json"
    media_dir = source_dir / "media"
    if not graph_path.is_file() or not media_dir.is_dir():
        raise ValueError("The exported StoryCanvas graph and media directory are required")

    repository_root = Path(__file__).resolve().parents[1]
    template = repository_root / "storycanvas_harness" / "templates" / "pipeline_demo.html"
    if not template.is_file():
        raise ValueError(f"Missing pipeline demo template: {template}")

    video_output.parent.mkdir(parents=True, exist_ok=True)
    poster_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="storycanvas-pipeline-demo-") as temp_name:
        site = Path(temp_name) / "site"
        site.mkdir()
        shutil.copy2(template, site / "pipeline_demo.html")
        shutil.copy2(graph_path, site / "canvas_graph.json")
        try:
            (site / "media").symlink_to(media_dir.resolve(), target_is_directory=True)
        except OSError:
            shutil.copytree(media_dir, site / "media")

        server, url = _serve(site)
        try:
            recorded_dir = Path(temp_name) / "recorded"
            poster_png = Path(temp_name) / "poster.png"
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
                    lambda message: (
                        poster_errors.append(message.text) if message.type == "error" else None
                    ),
                )
                poster_page.on("pageerror", lambda error: poster_errors.append(str(error)))
                poster_page.on("request", lambda request: poster_requests.append(request.url))
                poster_page.goto(f"{url}?final=1", wait_until="networkidle")
                poster_page.wait_for_function(
                    "window.pipelineDemo.ready && window.pipelineDemo.stage === 5", timeout=30_000
                )
                poster_page.wait_for_timeout(900)
                poster_page.screenshot(path=str(poster_png), full_page=False)
                if poster_errors:
                    raise RuntimeError(f"Pipeline demo console errors: {poster_errors}")
                external = [
                    request for request in poster_requests if not _is_local(request, origin)
                ]
                if external:
                    raise RuntimeError(f"Pipeline demo made non-local requests: {external}")
                poster_context.close()

                video_context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    device_scale_factor=1,
                    record_video_dir=str(recorded_dir),
                    record_video_size={"width": 1920, "height": 1080},
                )
                page = video_context.new_page()
                errors: list[str] = []
                requests: list[str] = []
                page.on(
                    "console",
                    lambda message: (
                        errors.append(message.text) if message.type == "error" else None
                    ),
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.goto(f"{url}?demo=1", wait_until="networkidle")
                page.wait_for_function("window.pipelineDemo.done", timeout=35_000)
                page.wait_for_timeout(350)
                raw_video = page.video
                video_context.close()
                browser.close()
                if errors:
                    raise RuntimeError(f"Pipeline demo console errors: {errors}")
                external = [request for request in requests if not _is_local(request, origin)]
                if external:
                    raise RuntimeError(f"Pipeline demo made non-local requests: {external}")
                raw_path = Path(raw_video.path())

            with Image.open(poster_png) as image:
                image.save(poster_output, "WEBP", quality=88, method=6)

            _run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    "0.15",
                    "-i",
                    str(raw_path),
                    "-t",
                    "23.8",
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
        description="Render the editorial StoryCanvas prompt-to-video pipeline demo."
    )
    parser.add_argument("--source-dir", type=Path, default=Path("examples/moon_garden_canvas"))
    parser.add_argument(
        "--video-output",
        type=Path,
        default=Path("assets/demo/storycanvas-pipeline-demo-v2.mp4"),
    )
    parser.add_argument(
        "--poster-output",
        type=Path,
        default=Path("assets/demo/storycanvas-pipeline-demo-v2-poster.webp"),
    )
    args = parser.parse_args()
    render(
        args.source_dir.resolve(),
        args.video_output.resolve(),
        args.poster_output.resolve(),
    )


if __name__ == "__main__":
    main()
