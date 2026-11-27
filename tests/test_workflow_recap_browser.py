"""A workflow's recap in a real browser (#1190): above the steps in the
transcript, the viewer and a docked pane, at desktop and phone width, and
paid for only while an expanded card intersects a visible tab."""

import json

import pytest

from .test_browser import (  # noqa: F401
    CONFIG,
    Server,
    browser,
    new_page,
    page,
    spawn,
)
from .test_workflows_e2e import RESULT, flow

pytestmark = [pytest.mark.browser, pytest.mark.slow]

SLOW = f"""id: slow
title: Slow run
say: One step finishes, then a pause, then another.
root:
  seq:
    - agent: {{agent: opus, prompt: {json.dumps(RESULT)},
              result: {{type: object, properties: {{n: {{type: integer}}}}, required: [n]}}}}
      id: one
      say: Do the first thing.
    - {{sh: "sleep 8", id: pause, say: Pause.}}
    - agent: {{agent: opus, prompt: {json.dumps(RESULT)}}}
      id: two
      say: Do the last thing.
  say: Both things.
"""


@pytest.fixture
def server(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "recap: {agent: opus}\n")
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def calls(server) -> int:
    log = server.root / "fake-home" / "oneshot.log"
    return len(log.read_text().split()) if log.exists() else 0


def arm(pg, run: str) -> None:
    pg.fill(
        "#input", "/mcp workflow_arm " + json.dumps({"definition": run, "args": {}})
    )
    pg.press("#input", "Enter")


def settled(pg, status: str, timeout: int = 20000) -> None:
    pg.wait_for_selector(f".wfcard[data-status={status}]", timeout=timeout)


def test_the_recap_sits_above_the_steps_and_approval_still_works(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        page,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    settled(page, "armed")
    # The card is visible while it waits for the go: one recap for that state.
    page.wait_for_selector(".wfcard .wfrecap")
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    settled(page, "done")
    page.wait_for_function(
        "() => document.querySelector('.wfcard[data-status=done] .wfrecap')"
        "?.textContent.includes('ended done')",
        timeout=20000,
    )
    text = page.locator(".wfcard[data-status=done] .wfrecap").inner_text()
    assert "ended done" in text, text
    assert "finished" in text, text
    # It precedes the first step row in the card.
    before = page.evaluate(
        """() => {
          const c = document.querySelector('.wfcard[data-status=done]');
          const r = c.querySelector('.wfrecap'), t = c.querySelector('.wftree');
          return !!(r.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING);
        }"""
    )
    assert before
    # The approval control worked and the step rows are still there.
    assert page.locator(".wfcard .wfrow").count() >= 3
    assert page.locator(".wfcard .apbar button").count() == 0  # a done run
    # The same paragraph is in the viewer's full-size card.
    page.click(".wfcard[data-status=done] .btn.view")
    page.wait_for_selector("#viewer .wfcard .wfrecap")
    assert "ended done" in page.locator("#viewer .wfcard .wfrecap").inner_text()
    page.click("#viewer .vclose")
    assert not page.errors


def test_the_recap_docks_and_fits_a_phone(server, browser):  # noqa: F811
    errors: list = []
    pg = new_page(browser, errors)
    pg.set_viewport_size({"width": 1300, "height": 900})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        pg,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    settled(pg, "armed")
    pg.click(".wfcard[data-status=armed] .btn.dock")
    pane = ".pane[data-pane^=wf-] .wfcard"
    pg.wait_for_selector(f"{pane} .wfrecap")
    assert pg.locator("#entries .wfcard .wfrecap").count() == 0  # one line while docked
    pg.click(f"{pane}[data-status=armed] button >> text=Approve and run")
    pg.wait_for_selector(f"{pane}[data-status=done] .wfrecap", timeout=20000)

    phone = new_page(browser, errors)
    phone.set_viewport_size({"width": 390, "height": 800})
    phone.goto(server.url)
    phone.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        phone,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    settled(phone, "armed")
    phone.locator(".wfcard").scroll_into_view_if_needed()
    phone.wait_for_selector(".wfcard .wfrecap")
    phone.click(".wfcard[data-status=armed] button >> text=Approve and run")
    settled(phone, "done")
    phone.locator(".wfcard").scroll_into_view_if_needed()
    phone.wait_for_function(
        "() => document.querySelector('.wfcard[data-status=done] .wfrecap')"
        ".textContent.includes('ended done')",
        timeout=20000,
    )
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390
    assert not errors


def test_a_hidden_tab_pays_nothing_and_catches_up_on_return(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    lid = spawn(
        page, "/mcp workflow_arm " + json.dumps({"definition": SLOW, "args": {}})
    )
    settled(page, "armed")
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    page.wait_for_selector(".wfcard .wfrecap")
    # Leave the run's tab: its card is no longer visible.
    spawn(page, "hello")
    page.wait_for_timeout(500)
    paid = calls(server)
    assert paid >= 1
    page.wait_for_timeout(9000)  # the run ends while nobody reads it
    assert calls(server) == paid
    page.click(f"#tablist .tab[data-id='{lid}']")
    page.wait_for_selector(".wfcard[data-status=done] .wfrecap", timeout=20000)
    page.wait_for_function(
        "() => document.querySelector('.wfcard[data-status=done] .wfrecap')"
        ".textContent.includes('ended done')",
        timeout=20000,
    )
    assert calls(server) > paid
    assert not page.errors


def test_a_saved_recap_with_the_json_wrapper_shows_as_a_paragraph(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    lid = spawn(
        page,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    settled(page, "armed")
    run = page.locator(".wfcard").first.get_attribute("data-run")

    # What an earlier build saved (#1194): the recap's own JSON envelope.
    server.stop()
    state = server.root / ".aegis" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "workflow-recaps.json").write_text(
        json.dumps(
            {
                run: {
                    "input": "an older state",
                    "text": json.dumps(
                        {"text": "La revisión está pendiente."}, ensure_ascii=False
                    ),
                    "at": 0,
                    "model": "opus",
                }
            },
            ensure_ascii=False,
        )
    )
    # The catch-up call must not replace the text before the test reads it.
    server.env.update(
        {"FAKE_CLAUDE_ONESHOT": "slow", "FAKE_CLAUDE_ONESHOT_SLEEP": "120"}
    )
    server.start()
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click(f"#tablist .tab[data-id='{lid}']")
    page.wait_for_selector(".wfcard .wfrecap")
    text = page.locator(".wfcard .wfrecap").inner_text()
    assert text.startswith("La revisión está pendiente."), text
    assert '{"text"' not in text, text
    assert not page.errors


def test_the_icon_only_reload_button_regenerates_the_recap(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        page,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    settled(page, "armed")
    page.wait_for_selector(".wfcard .wfrecap")
    button = page.locator(".wfcard .wfrecap .reload")
    assert button.count() == 1
    assert button.get_attribute("aria-label")
    assert button.get_attribute("title")
    before = page.locator(".wfcard .wfrecap").inner_text()
    paid = calls(server)
    button.click()
    # A current state still costs one generation: the button forces it.
    for _ in range(100):
        if calls(server) > paid:
            break
        page.wait_for_timeout(100)
    assert calls(server) > paid
    page.wait_for_selector(".wfcard .wfrecap")
    text = page.locator(".wfcard .wfrecap").inner_text()
    assert '{"text"' not in text
    assert text.startswith(before.split("updating")[0].split("stale")[0])
    assert not page.errors
