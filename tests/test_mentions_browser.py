# ruff: noqa: F811  (fixtures imported from test_browser)
"""@ mentions in the client: the composer's menu and the transcript's block."""

import pytest

from .test_browser import browser, page, server, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def rows(pg) -> list[tuple[str, str]]:
    pg.wait_for_selector("#cmd-menu .cmd-row", state="visible")
    return [
        (
            r.query_selector(".nm").text_content(),
            r.query_selector(".src").text_content(),
        )
        for r in pg.query_selector_all("#cmd-menu .cmd-row")
    ]


def two_sessions(pg, server) -> str:
    """Two sessions, the second shown; the handle of the first."""
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg, "first")
    spawn(pg, "second")
    pg.click("#input")
    pg.keyboard.type("@")
    (other,) = [n for n, src in rows(pg) if src.startswith("session")]
    pg.keyboard.press("Escape")
    pg.fill("#input", "")
    return other.removeprefix("@")


def test_at_lists_agents_and_sessions_and_a_note_shows_as_a_block(server, page):
    other = two_sessions(page, server)
    page.click("#input")
    page.keyboard.type("@")
    names = rows(page)
    assert ("@opus", "agent") in names and ("@deepseek", "agent") in names
    assert (f"@{other}", "session") in names
    page.keyboard.type(other[:4])
    page.keyboard.press("Tab")
    assert page.input_value("#input") == f"@{other} "
    page.keyboard.type("the flaky test is in test_tabs.py")
    page.keyboard.press("Enter")
    page.wait_for_selector(".row.mention .chip")
    assert (
        page.text_content(".row.mention .hd .q") == "the flaky test is in test_tabs.py"
    )
    assert page.text_content(".row.mention .chip").startswith(other)
    assert page.input_value("#input") == ""
    assert page.errors == []


def test_mid_line_at_completes_only_a_session_and_keeps_the_text(server, page):
    other = two_sessions(page, server)
    page.click("#input")
    page.keyboard.type("ask @")
    names = rows(page)
    assert names and all(src.startswith("session") for _, src in names)
    page.keyboard.press("Tab")
    assert page.input_value("#input") == f"ask @{other} "
    assert page.errors == []


def test_an_agent_answers_into_the_block_and_an_unknown_name_is_flagged(server, page):
    two_sessions(page, server)
    page.fill("#input", "@nobody-here hi")
    page.wait_for_selector(".composer.bad")
    page.fill("#input", "@opus is this migration safe?")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.mention .ans", timeout=20000)
    assert page.text_content(".row.mention .ans .who") == "opus"
    assert "answered" in page.text_content(".row.mention .chip")
    assert page.errors == []


def test_a_committee_merge_can_be_skipped(server, page):
    other = two_sessions(page, server)
    page.fill("#input", f"@opus @{other} review this plan")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.mention [data-mention-skip]")
    assert page.text_content(".row.mention .hd span") == "committee"
    page.click(".row.mention [data-mention-skip]")
    page.wait_for_selector('.row.mention .ft:has-text("merge skipped")')
    assert not page.query_selector(".row.mention [data-mention-skip]")
    assert page.errors == []


MENTIONABLE = """import('/static/js/parts/commands.js').then((m) => m.mentionable(new Map([
  ["me", {handle: "me", state: "idle"}],
  ["a", {handle: "ana", title: "docs", state: "working"}],
  ["w", {handle: "run-1", state: "working", worker: {task_id: "t"}}],
  ["t", {handle: "sh", kind: "term"}],
  ["x", {handle: "old", archived: true}],
  ["lab/b", {handle: "ben", state: "idle"}],
]), "me"))"""


def test_the_menu_offers_neither_itself_workers_tools_nor_far_sessions(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.evaluate(MENTIONABLE) == [
        {"handle": "ana", "title": "docs", "busy": True}
    ]
