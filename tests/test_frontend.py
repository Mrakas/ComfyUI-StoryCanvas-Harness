from __future__ import annotations

import os
from pathlib import Path

import pytest

from storycanvas_harness.onboarding import run_demo

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("STORYCANVAS_BROWSER_TESTS") != "1",
        reason="Enable STORYCANVAS_BROWSER_TESTS=1 after installing the demo extra and Chromium",
    ),
]


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def test_canvas_media_controls_and_mobile_layout(browser, tmp_path: Path) -> None:
    report = run_demo(tmp_path, with_video=True)
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(Path(report["viewer"]).as_uri() + "?final=1")
    page.wait_for_function(
        "[...document.images].every(image => image.complete && image.naturalWidth > 0)"
    )
    assert page.locator("video").count() == 4
    page.wait_for_function(
        "[...document.querySelectorAll('video')].every(video => video.readyState >= 1)"
    )
    page.locator(".node.visible").first.focus()
    page.keyboard.press("Enter")
    assert page.locator(".details").evaluate("element => element.classList.contains('open')")
    page.keyboard.press("Escape")
    assert not page.locator(".details").evaluate("element => element.classList.contains('open')")
    page.locator("#play").click()
    page.locator("#next").click()
    assert page.locator("#play").inner_text() == "▶ Play"
    page.locator("#reset").click()
    assert page.evaluate("window.storyCanvasDemo.stage") == 0
    assert page.evaluate(
        "[...document.querySelectorAll('video')].every(video => video.paused && video.currentTime === 0)"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("window.storyCanvasDemo.setStage(6)")
    page.wait_for_timeout(350)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for control in ("#reset", "#next", "#play"):
        box = page.locator(control).bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
    first = page.locator(".node.visible").first.bounding_box()
    assert first and first["width"] > 340
    assert (
        page.locator(".node.visible h2").first.evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        )
        >= 16
    )
    page.locator(".node.visible").last.scroll_into_view_if_needed()
    page.locator(".node.visible").last.locator("h2").click()
    assert page.locator(".details").evaluate("element => element.classList.contains('open')")
    assert page.locator("#detail-close").is_visible()
    assert errors == []
    page.close()


def test_comfy_preview_ignores_stale_responses_and_applies_snapshot(browser) -> None:
    page = browser.new_page()
    page.set_content("<html><head></head><body></body></html>")
    source = (
        (ROOT / "web/js/storycanvas.js")
        .read_text()
        .replace('import { app } from "../../scripts/app.js";', "const app = window.testApp;")
    )
    page.evaluate("""() => {
        window.testApp = { registerExtension: value => window.extension = value, loadGraphData: async graph => window.loadedGraph = graph };
        window.requests = [];
        window.fetch = (path, options) => new Promise(resolve => window.requests.push({path, payload: JSON.parse(options.body), resolve}));
    }""")
    page.add_script_tag(content=source)
    page.evaluate("window.extension.commands[0].function()")
    page.locator("[data-build]").click()
    assert page.locator("[data-build]").is_disabled()
    page.locator("[name=prompt]").fill("A new fictional story")
    plan = {
        "plan_id": "plan-test",
        "story_id": "story-test",
        "title": "Story",
        "shots": [{"order": 1}],
        "warnings": [],
        "call_estimate": {
            "planning_calls": 1,
            "fact_search_calls": 0,
            "image_generation_calls": 2,
            "video_generation_calls": 1,
            "paid_video_locked": True,
        },
    }
    page.evaluate("plan => window.requests[0].resolve({ok: true, json: async () => plan})", plan)
    assert page.locator("[data-apply]").is_disabled()
    page.locator("[data-build]").click()
    page.evaluate("plan => window.requests[1].resolve({ok: true, json: async () => plan})", plan)
    page.wait_for_function("!document.querySelector('[data-apply]').disabled")
    page.locator("[data-apply]").click()
    payload = page.evaluate("window.requests[2].payload")
    assert payload["plan"] == plan
    assert "plan_id" not in payload
    page.evaluate(
        "window.requests[2].resolve({ok: true, json: async () => ({workflow: {version: 0.4}})})"
    )
    page.wait_for_function("window.loadedGraph?.version === 0.4")
    page.close()
