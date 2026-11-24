# ruff: noqa: F811
"""Both entry points show the real selection card; the person's click deletes."""

from pathlib import Path

import pytest

from .test_browser import browser, page, server, spawn  # noqa: F401
from .test_canvas_browser import mcp
from .test_repo_gc import repository

pytestmark = [pytest.mark.browser, pytest.mark.slow]
CARD = ".row.gc .gccard"


def test_slash_selection_approval_and_mcp_cancellation(server, page):
    repo = repository(server.root)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/gc")
    page.press("#input", "Enter")
    page.wait_for_selector(f"{CARD} input[type=checkbox]")
    assert (repo / "target").exists(), "a slash command only proposes"
    boxes = page.locator(f"{CARD} label")
    assert boxes.count() == 3
    for label in boxes.all():
        label.locator("input").set_checked(
            label.locator("code").inner_text().endswith("/target")
        )
    # The same card and selection remain usable after a browser reload.
    page.reload()
    page.wait_for_selector(f"{CARD} input[type=checkbox]")
    # Local unsent checkbox state is intentionally not an approved selection.
    for label in page.locator(f"{CARD} label").all():
        label.locator("input").set_checked(
            label.locator("code").inner_text().endswith("/target")
        )
    shots = Path(".playground/gc-1127-shots")
    shots.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": 1280, "height": 1300})
    page.mouse.move(0, 0)
    page.locator(CARD).screenshot(path=str(shots / "desktop.png"))
    page.set_viewport_size({"width": 390, "height": 1300})
    page.mouse.move(0, 0)
    page.wait_for_function(
        "document.querySelector('.v-session > .side').getBoundingClientRect().left >= innerWidth - 1"
    )
    page.locator(CARD).screenshot(path=str(shots / "phone.png"))
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.click(f"{CARD} button:has-text('Delete selected directories')")
    page.wait_for_function(
        "s => document.querySelector(s)?.textContent.includes('Cleanup finished.')",
        arg=CARD,
    )
    assert not (repo / "target").exists()
    assert (repo / ".venv").exists() and (repo / "node_modules").exists()
    # hello, the approval wake, and this real MCP call each finish one turn.
    mcp(page, 3, "gc_propose", {})
    last = page.locator(CARD).last
    last.locator("button:has-text('Cancel')").click()
    page.wait_for_function(
        "s => [...document.querySelectorAll(s)].at(-1)?.textContent.includes('Cancelled; nothing deleted.')",
        arg=CARD,
    )
    assert (repo / ".venv").exists() and (repo / "node_modules").exists()
    assert page.errors == []
