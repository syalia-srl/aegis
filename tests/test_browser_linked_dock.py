# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""A linked server's session seen from the home server, with what it docked:
a sent image draws through /via, and its browser streams through the link
(#970, canvas.js and app.js)."""

import base64

import pytest

from .test_browser import (  # noqa: F401
    browser,
    linked,
    new_page,
    page,
    spawn,
)
from .test_browsers import site  # noqa: F401
from .test_canvas_browser import mcp

pytestmark = [pytest.mark.browser, pytest.mark.slow]

# A 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def far_session(linked, browser, *calls) -> str:
    """A session on beta that runs each (tool, args) from beta's own page; its
    log id."""
    errors: list = []
    pg = new_page(browser, errors)
    pg.goto(linked.beta.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    lid = spawn(pg)
    for n, (tool, args) in enumerate(calls, start=1):
        mcp(pg, n, tool, args)
    pg.close()
    return lid


def test_a_far_sessions_docked_image_draws_through_via(linked, browser, page):
    img = linked.beta.root / "dot.png"
    img.write_bytes(PNG)
    lid = far_session(
        linked, browser, ("file_send", {"paths": [str(img)], "dock": "right"})
    )
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    pane = page.locator("#canvas .pane.docked img")
    pane.wait_for(timeout=10_000)
    src = pane.get_attribute("src")
    assert src.startswith("/via/beta/files/") and src.endswith("/dot.png"), src
    page.wait_for_function(
        "() => document.querySelector('#canvas .pane.docked img').naturalWidth > 0"
    )
    assert not page.errors


def test_a_far_sessions_docked_browser_streams_through_the_link(
    linked, browser, page, site
):
    lid = far_session(linked, browser, ("browser_open", {"url": site}))
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    pane = page.locator("#canvas .pane.docked[data-pane^='b-'] .br-view")
    pane.wait_for(timeout=10_000)
    pane.locator(".br-wait").wait_for(state="hidden", timeout=15_000)
    assert pane.locator(".br-holder").inner_text() == "agent"
    assert not page.errors


def test_a_far_read_rows_file_is_fetched_through_the_link(linked, browser, page):
    # The Show button on a linked server's Read row (#1044): it names the
    # server, beta copies its own file, and the card draws it through /via.
    from .test_browser import turns_done

    notes = linked.beta.root / "far-notes.txt"
    notes.write_text("one\ntwo\n")
    errors: list = []
    pg = new_page(browser, errors)
    pg.goto(linked.beta.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    lid = spawn(pg)
    pg.fill("#input", f"/read {notes}")
    pg.press("#input", "Enter")
    turns_done(pg, 1)
    pg.close()
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    row = page.locator(".row.tool")
    row.locator("summary").click()
    peek = row.locator(".peek")
    assert peek.inner_text().strip() == "Fetch the file from beta"
    peek.click()
    card = row.locator(".fcard")
    card.wait_for(timeout=10_000)
    href = card.locator("a.dl").get_attribute("href")
    assert href.startswith("/via/beta/files/") and "far-notes.txt" in href, href
    assert row.locator(".peek").inner_text().strip() == "Fetch the file from beta again"
    assert not page.errors
