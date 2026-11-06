# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The sign-in card in a browser (#946): a failed Claude turn opens it, the
code typed on it reaches the login (tests/fake_login.py through the fake
claude's setup-token), and the waiting session restarts."""

import os
from pathlib import Path

import pytest

from .test_browser import (  # noqa: F401
    browser,
    online,
    page,
    server,
    settings_server,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

CARD = ".row.sign_in .scard"
BAD = "sk-ant-oat01-bad" + "x" * 80


def start_failing_turn(pg) -> None:
    """Spawn a session whose first turn fails to sign in; it never ends in
    "done in", so test_browser.spawn would wait for it."""
    online(pg)
    pg.click("#tab-add")
    pg.wait_for_selector("#a2[data-view=spawn]")
    pg.wait_for_function("document.querySelector('#sp-agent').value !== ''")
    pg.fill("#sp-text", "hello")
    pg.click("#sp-go")
    pg.wait_for_selector("#a2[data-view=session]")


def put_bad_token() -> Path:
    p = Path(os.environ["AEGIS_CLAUDE_TOKEN_FILE"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(BAD + "\n")
    p.chmod(0o600)
    return p


def test_a_code_typed_on_the_card_signs_in_and_restarts_the_session(server, page):
    token = put_bad_token()
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    start_failing_turn(page)
    page.wait_for_selector(f"{CARD} a:has-text('Open sign-in page')", timeout=15000)
    href = page.get_attribute(f"{CARD} a:has-text('Open sign-in page')", "href")
    assert href.startswith("https://claude.com/cai/oauth/authorize?")
    term = page.get_attribute(f"{CARD} a:has-text('Show terminal')", "href")
    assert term.startswith("#t=")

    page.set_viewport_size({"width": 390, "height": 800})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, width
    page.set_viewport_size({"width": 1280, "height": 900})

    page.fill(f"{CARD} input[aria-label='sign-in code']", "FAKECODE")
    page.click(f"{CARD} button:has-text('Sign in')")
    page.wait_for_selector(f"{CARD}.gone:has-text('Signed in')", timeout=20000)
    assert "Restarted" in page.inner_text(CARD)
    assert token.read_text().startswith("sk-ant-oat01-FAKE")
    assert "sk-ant-oat01-FAKE" not in page.content()
    assert page.errors == []


def test_show_terminal_opens_the_masked_login(server, page):
    put_bad_token()
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    start_failing_turn(page)
    page.wait_for_selector(f"{CARD} a:has-text('Show terminal')", timeout=15000)
    page.click(f"{CARD} a:has-text('Show terminal')")
    page.wait_for_selector("#a2[data-view=term]")
    page.wait_for_function(
        "() => /Paste code here/.test(document.querySelector('#a2').innerText)",
        timeout=10000,
    )
    assert page.errors == []


def test_sign_in_in_settings_opens_the_card_in_the_last_session(settings_server, page):
    page.goto(settings_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    page.keyboard.press("Alt+KeyS")
    page.wait_for_selector("#a2[data-view=settings]")
    row = '#set-harnesses .set-harness[data-harness="claude-code"]'
    page.wait_for_selector(f"{row} button:has-text('Sign in')")
    assert "its own sign-in" in page.inner_text(row)
    page.set_viewport_size({"width": 390, "height": 800})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, width
    page.set_viewport_size({"width": 1280, "height": 900})
    page.click(f"{row} button:has-text('Sign in')")
    page.wait_for_selector("#a2[data-view=session]")
    assert page.evaluate("location.hash.slice(3)") == sid
    page.wait_for_selector(f"{CARD} a:has-text('Open sign-in page')", timeout=15000)
    assert page.errors == []
