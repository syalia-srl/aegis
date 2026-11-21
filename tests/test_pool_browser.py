"""The sidebar draws the quota of the pool a session's card names (#1113),
not the one its harness implies: a claude-code session whose record names
OpenCode Go shows Go's gauge, at desktop and at phone width."""

import json
from pathlib import Path

import pytest

from .test_browser import (  # noqa: F401
    TILES,
    browser,
    online,
    page,
    quota_server,
    spawn_agent,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

CLAUDE = "context,session,weekly_all"
GO = "context,rolling"


def _meta(root: Path, key: str) -> Path:
    """The meta file of the session the client opened as ``key``."""
    for f in (root / ".aegis" / "state" / "sessions").glob("*.json"):
        m = json.loads(f.read_text())
        if key in (m.get("log_id"), m.get("handle")):
            return f
    raise AssertionError(f"no meta for {key}")


def _open(pg, url: str, key: str, width: int) -> None:
    pg.set_viewport_size({"width": width, "height": 800})
    pg.goto(f"{url}#s={key}")
    online(pg)
    pg.wait_for_selector("#a2[data-view=session]")
    if width < 600:
        pg.wait_for_selector("#nav:not([hidden])")
        pg.click("#nav-side")
        pg.wait_for_function("document.getElementById('a2').dataset.side === 'open'")


def _shows(pg, tiles: str, head: str, width: int) -> None:
    pg.wait_for_function(f"{TILES}.join() === {tiles!r}")
    assert pg.locator("#s-quota-head").text_content() == head
    if width >= 600:  # the card behind the row opens on a hover
        pg.hover("#p-usage")
        pg.wait_for_selector("#p-usage .pcard", state="visible")
        assert pg.inner_text("#s-quota-head") == head
        pg.mouse.move(5, 300)
    else:
        assert pg.evaluate("document.documentElement.scrollWidth") <= width


def test_a_claude_code_session_on_opencode_go_draws_gos_gauge(quota_server, page):  # noqa: F811
    s = quota_server
    page.goto(s.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn_agent(page, "opus")
    on_claude = page.evaluate("location.hash.slice(3)")
    spawn_agent(page, "opus")
    on_go = page.evaluate("location.hash.slice(3)")
    assert on_claude != on_go

    # Nothing in this slice sets a pool, so write one on the record, as a
    # connection agent's spawn will (B1); a boot reads it back from there.
    s.stop()
    f = _meta(s.root, on_go)
    m = json.loads(f.read_text())
    assert m["harness"] == "claude-code" and "pool" not in m
    f.write_text(json.dumps({**m, "pool": "opencode-go"}))
    s.start()

    for width in (1280, 390):
        _open(page, s.url, on_go, width)
        _shows(page, GO, "Quota · OpenCode Go", width)
        _open(page, s.url, on_claude, width)
        _shows(page, CLAUDE, "Quota · Claude", width)
    assert page.errors == []
