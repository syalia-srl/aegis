"""The latest request, kept over the top of the reading area (#1196).

A real ``aegis serve`` and the fake claude, as the rest of the browser suite:
the scenarios follow the spec's AC-1 to AC-5, including a reopened transcript
whose latest user row is above the mounted window.
"""

import pytest

from .test_browser import browser, new_page, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


PIN = """() => {
  const p = document.querySelector('.reqpin');
  if (!p) return null;
  const t = p.querySelector('.reqpin-text');
  const m = p.querySelector('.reqpin-msg');
  return {on: !p.hidden, text: m.textContent, role: t.getAttribute('role'),
          expanded: t.getAttribute('aria-expanded'),
          more: !p.querySelector('.reqpin-more').hidden};
}"""

PIN_ON = """() => {
  const p = document.querySelector('.reqpin');
  return !!p && !p.hidden && p.querySelector('.reqpin-msg').textContent.length > 0;
}"""

SET_GAP = """(d) => {
  const t = document.getElementById('tr');
  const r = [...document.querySelectorAll('#entries > .row.user')].at(-1);
  t.scrollTop += r.getBoundingClientRect().bottom - t.getBoundingClientRect().top - d;
}"""


def pin(pg):
    return pg.evaluate(PIN)


def wait_pin(pg, on=True):
    if on:
        pg.wait_for_function(PIN_ON, timeout=8000)
    else:
        pg.wait_for_function(
            "() => { const p = document.querySelector('.reqpin'); return !p || p.hidden; }",
            timeout=8000,
        )


def send(pg, text):
    done = pg.evaluate(
        "() => [...document.querySelectorAll('.row.sys .body, .row.error .body')]"
        ".filter(b => /^(done in|interrupted)/.test(b.textContent)).length"
    )
    pg.fill("#input", text)
    pg.press("#input", "Enter")
    pg.wait_for_function(
        "n => [...document.querySelectorAll('.row.sys .body, .row.error .body')]"
        ".filter(b => /^(done in|interrupted)/.test(b.textContent)).length >= n",
        arg=done + 1,
        timeout=60_000,
    )


def edge(pg, d):
    pg.evaluate(SET_GAP, d)
    pg.wait_for_timeout(120)


def test_the_request_pins_at_the_last_pixel_and_expands_in_place(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/many 140")

    # A short request needs no disclosure and is one line.
    wait_pin(page)
    state = pin(page)
    assert state["text"] == "/many 140" and state["role"] is None and not state["more"]
    assert "out 0" not in state["text"] and "step 0" not in state["text"]

    # The row is above the reading area at the bottom; one pixel of it puts the
    # header away, the last pixel out brings it back.
    edge(page, 1)
    wait_pin(page, on=False)
    edge(page, -1)
    wait_pin(page)

    # A long message: markup stays literal, an unbroken run wraps, and the
    # header is two clamped lines with a keyboard-operable disclosure.
    tail = "lastword"
    request = (
        "Review the release notes for both versions.\n\n"
        "<b>not bold</b> and &amp; stay literal.\n\n"
        + ("group the changes by the part of the application they affect " * 28)
        + " "
        + "x" * 600
        + " "
        + tail
    )
    send(page, request)
    wait_pin(page)
    state = pin(page)
    assert state["text"] == request
    assert state["role"] == "button" and state["expanded"] == "false" and state["more"]
    assert page.locator(".reqpin-msg b").count() == 0

    two = page.evaluate(
        """() => {
          const m = document.querySelector('.reqpin-msg');
          return {h: m.clientHeight, lh: parseFloat(getComputedStyle(m).lineHeight),
                  clamped: m.classList.contains('clamp')};
        }"""
    )
    assert two["clamped"] and two["h"] <= two["lh"] * 2 + 2, two

    # Keyboard opens it; the whole paste is reachable and scrolls inside half
    # the pane. Collapsing leaves the reading position exactly where it was.
    before = page.evaluate("document.getElementById('tr').scrollTop")
    page.locator(".reqpin-text").press("Enter")
    assert pin(page)["expanded"] == "true"
    page.wait_for_timeout(120)
    box = page.evaluate(
        """() => {
          const t = document.querySelector('.reqpin-text');
          const tr = document.getElementById('tr');
          const m = document.querySelector('.reqpin-msg');
          m.scrollTop = m.scrollHeight;
          return {h: t.clientHeight, pane: tr.clientHeight,
                  bottom: m.textContent.trim().split(/\\s+/).at(-1),
                  scrolled: m.scrollTop,
                  less: !document.querySelector('.reqpin-more').hidden};
        }"""
    )
    assert box["bottom"] == tail and box["scrolled"] > 0, box
    assert box["h"] <= box["pane"] / 2 + 1, box
    page.locator(".reqpin-text").click()
    page.wait_for_timeout(120)
    assert pin(page)["expanded"] == "false"
    assert abs(page.evaluate("document.getElementById('tr').scrollTop") - before) <= 1

    # Phone width and rotation: still inside the pane, still disclosure.
    for width, height in ((390, 760), (844, 390), (390, 760)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        rect = page.evaluate(
            """() => {
              const p = document.querySelector('.reqpin').getBoundingClientRect();
              const pane = document.getElementById('tr').parentElement.getBoundingClientRect();
              return {l: p.left, r: p.right, t: p.top, b: p.bottom,
                      pl: pane.left, pr: pane.right, pt: pane.top, pb: pane.bottom};
            }"""
        )
        assert (
            rect["l"] >= rect["pl"] - 0.5
            and rect["r"] <= rect["pr"] + 0.5
            and rect["t"] >= rect["pt"] - 0.5
            and rect["b"] <= rect["pb"] + 0.5
        ), (width, height, rect)
        state = pin(page)
        assert state["on"] and state["role"] == "button", (width, height, state)
    assert page.errors == []


def test_a_new_request_takes_over_once_it_passes_the_top_and_sessions_keep_their_own(
    server,  # noqa: F811
    page,  # noqa: F811
):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "/many 120")

    first = "the first request, " * 400
    send(page, first)
    wait_pin(page)
    page.locator(".reqpin-text").click()
    assert pin(page)["expanded"] == "true"

    # A new message below the reading area does not take over. The request
    # above it keeps the header, open state and all, until its own row
    # passes above the top (#1198).
    page.fill("#input", "/many 120")
    page.press("#input", "Enter")
    page.wait_for_function(
        "() => document.querySelectorAll('#entries > .row.user').length === 2"
    )
    page.wait_for_timeout(200)
    state = pin(page)
    assert (
        state["on"] and state["text"] == first.rstrip() and state["expanded"] == "true"
    ), state

    turns_done(page, 3)
    wait_pin(page)
    state = pin(page)
    assert state["text"] == "/many 120"
    assert state["role"] is None and not state["more"]

    # Each session evaluates its own candidate; an empty session has none.
    b = spawn(page, "/many 80")
    wait_pin(page)
    assert pin(page)["text"] == "/many 80"
    c = spawn(page)
    page.wait_for_timeout(300)
    assert not pin(page)["on"]
    page.click(f"#tablist .tab[data-id='{a}']")
    page.wait_for_timeout(300)
    assert pin(page)["text"] == "/many 120"
    page.click(f"#tablist .tab[data-id='{b}']")
    page.wait_for_timeout(300)
    assert pin(page)["text"] == "/many 80"
    page.click(f"#tablist .tab[data-id='{c}']")
    page.wait_for_timeout(300)
    assert not pin(page)["on"]
    assert page.errors == []


def test_an_attachment_only_request_pins_its_file_name(server, page, tmp_path):  # noqa: F811
    page.set_viewport_size({"width": 390, "height": 640})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)

    names = []
    long = "release-notes-for-the-next-two-versions-of-aegis-" * 4
    for i in range(20):
        f = tmp_path / f"{i:02d}-{long}.txt"
        f.write_text("x")
        names.append(f)
    page.set_input_files("#attach-pick", [str(f) for f in names])
    page.wait_for_function(
        "n => document.querySelectorAll('#atts .chip.att.done').length === n",
        arg=len(names),
        timeout=15000,
    )
    page.click("#send")
    page.wait_for_function(
        "() => [...document.querySelectorAll('.row.sys .body, .row.error .body')]"
        ".some(b => /^(done in|interrupted)/.test(b.textContent))",
        timeout=60_000,
    )
    wait_pin(page)
    state = pin(page)
    assert "00-release" in state["text"] and "19-release" in state["text"], state
    assert state["role"] == "button" and state["expanded"] == "false"

    # The reply lists the files, not the request: the tool and system rows of
    # a turn never become its text.
    assert "Attached files:" not in state["text"]
    rect = page.evaluate(
        """() => {
          const p = document.querySelector('.reqpin').getBoundingClientRect();
          const pane = document.getElementById('tr').parentElement.getBoundingClientRect();
          return {l: p.left, r: p.right, pl: pane.left, pr: pane.right};
        }"""
    )
    assert rect["l"] >= rect["pl"] - 0.5 and rect["r"] <= rect["pr"] + 0.5, rect
    assert page.errors == []


def test_a_reopened_transcript_pins_an_unmounted_request_until_its_row_returns(
    server,  # noqa: F811
    browser,  # noqa: F811
    page,  # noqa: F811
):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page, "/many 120")
    send(page, "/many 230")

    # At the tail the request is above the mounted window, so only its data
    # can pin it; the row is not in the DOM at all.
    page.reload()
    page.wait_for_function("window.__a2snapshot && window.__a2snapshot.painted")
    page.wait_for_function(PIN_ON)
    state = page.evaluate(
        """() => ({
          text: document.querySelector('.reqpin-msg').textContent,
          rows: document.querySelectorAll('#entries > .row.user').length,
          count: window.__a2snapshot.count,
        })"""
    )
    assert state["text"] == "/many 230" and state["rows"] == 0, state
    assert state["count"] > 350

    # Mount every earlier row. At the top of the transcript nothing has
    # passed above the reader, so no header.
    while page.locator("#entries > .row.user").count() < 2:
        page.evaluate("document.getElementById('tr').scrollTop = 0")
        page.wait_for_timeout(120)
    page.evaluate("document.getElementById('tr').scrollTop = 0")
    page.wait_for_timeout(200)
    wait_pin(page, on=False)

    # With the latest request's row in view, the request before it is the
    # nearest one above: the header shows that one, not nothing and not the
    # latest (which is still below).
    latest = page.locator("#entries > .row.user").last
    latest.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    state = pin(page)
    assert state["on"] and state["text"] == "/many 120", state

    # While the latest row is only partly past the top the earlier request
    # still pins; once the whole row is above, the latest takes over.
    edge(page, 1)
    state = pin(page)
    assert state["on"] and state["text"] == "/many 120", state
    edge(page, -1)
    wait_pin(page)
    assert pin(page)["text"] == "/many 230"

    # While following, the newest reply stays in view as the turn streams.
    page.evaluate("document.getElementById('tr').scrollTop = 1e9")
    page.wait_for_timeout(200)
    send(page, "/stream 40")
    gap = page.evaluate(
        "() => { const s = document.getElementById('tr');"
        " return s.scrollHeight - s.scrollTop - s.clientHeight; }"
    )
    assert gap < 48

    # A reader who scrolled up keeps their place when a new row arrives below.
    page.evaluate("document.getElementById('tr').scrollTop = 0")
    page.wait_for_timeout(200)
    anchor = page.evaluate(
        """() => {
          const t = document.getElementById('tr');
          const row = [...t.querySelectorAll('#entries > .row')]
            .find((r) => r.getBoundingClientRect().top >= t.getBoundingClientRect().top);
          return {id: row.dataset.id, top: row.getBoundingClientRect().top};
        }"""
    )
    # Output appended by another tab grows the list without moving this
    # reader, who did not send it.
    other = new_page(browser, [])
    other.goto(f"{server.url.split('#')[0]}#s={sid}")
    other.wait_for_selector("#a2[data-view=session]")
    send(other, "one more turn")
    page.wait_for_timeout(200)
    now = page.evaluate(
        """id => document.querySelector(`#entries > .row[data-id="${id}"]`)
                 .getBoundingClientRect().top""",
        anchor["id"],
    )
    assert abs(now - anchor["top"]) < 60, (anchor, now)
    assert page.errors == []


PLACE = """([i, at, d]) => {
  const t = document.getElementById('tr');
  const rows = [...document.querySelectorAll('#entries > .row.user')];
  const r = rows[i].getBoundingClientRect();
  const edge = at === 'bottom' ? r.bottom : r.top;
  t.scrollTop += edge - t.getBoundingClientRect().top + d;
  const now = rows[i].getBoundingClientRect();
  const top = t.getBoundingClientRect().top;
  return {scrollTop: t.scrollTop, top: now.top - top, bottom: now.bottom - top};
}"""


def brief(state):
    """A pin state fit for a failure message: no pasted prose in it."""
    if state is None:
        return None
    return {
        "on": state["on"],
        "head": state["text"][:24],
        "len": len(state["text"]),
        "role": state["role"],
        "expanded": state["expanded"],
        "more": state["more"],
    }


def place(pg, index, at, d=0):
    """Scroll so the reading area's top sits `d` px past a user row's edge."""
    state = None
    for _ in range(3):
        state = pg.evaluate(PLACE, [index, at, d])
        pg.wait_for_timeout(200)
    return state


def test_the_pin_is_the_nearest_request_above_the_reading_area(server, browser, page):  # noqa: F811
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    a = "/md A-request " + "one. " * 259 + "one."
    b = "/md B-request " + "two. " * 259 + "two."
    c = "/md C-request " + "three. " * 259 + "three."
    for text in (a, b, c):
        send(page, text)
    assert page.locator("#entries > .row.user").count() == 3

    # At the top of the transcript nothing has passed above: no header.
    page.evaluate("document.getElementById('tr').scrollTop = 0")
    page.wait_for_timeout(200)
    assert not pin(page)["on"], brief(pin(page))

    # Between A and B, with C already in the transcript: A is the nearest
    # request wholly above the reading area.
    state = place(page, 0, "bottom", 60)
    assert pin(page)["on"] and pin(page)["text"] == a, (state, brief(pin(page)))

    # B partly visible: still A.
    state = place(page, 1, "top", 8)
    assert pin(page)["text"] == a, (state, brief(pin(page)))

    # B wholly past the top: B.
    state = place(page, 1, "bottom", 8)
    assert pin(page)["text"] == b, (state, brief(pin(page)))

    # Back up: A again, symmetrically.
    state = place(page, 1, "top", 8)
    assert pin(page)["text"] == a, (state, brief(pin(page)))

    # An opened header collapses when the candidate itself changes.
    page.locator(".reqpin-text").click()
    assert pin(page)["expanded"] == "true"
    state = place(page, 1, "bottom", 8)
    now = pin(page)
    assert now["text"] == b and now["expanded"] == "false", (state, brief(now))

    # A message that arrives below this reader never replaces the candidate,
    # and the reader's place does not move.
    state = place(page, 0, "bottom", 60)
    anchor = page.evaluate(
        """() => {
          const t = document.getElementById('tr');
          const row = [...t.querySelectorAll('#entries > .row')]
            .find((r) => r.getBoundingClientRect().top >= t.getBoundingClientRect().top);
          return {id: row.dataset.id, top: row.getBoundingClientRect().top};
        }"""
    )
    other = new_page(browser, [])
    other.goto(f"{server.url.split('#')[0]}#s={sid}")
    other.wait_for_selector("#a2[data-view=session]")
    send(other, "a later request")
    page.wait_for_timeout(300)
    now = pin(page)
    assert now["on"] and now["text"] == a, (state, brief(now))
    moved = page.evaluate(
        """id => document.querySelector(`#entries > .row[data-id="${id}"]`)
                 .getBoundingClientRect().top""",
        anchor["id"],
    )
    assert abs(moved - anchor["top"]) < 60, (anchor, moved)

    # Phone width: the same rule on taller rows.
    page.set_viewport_size({"width": 390, "height": 760})
    page.wait_for_timeout(300)
    state = place(page, 0, "bottom", 40)
    now = pin(page)
    assert now["on"] and now["text"] == a, (state, brief(now))
    state = place(page, 1, "top", 8)
    assert pin(page)["text"] == a, (state, brief(pin(page)))
    state = place(page, 1, "bottom", 8)
    assert pin(page)["text"] == b, (state, brief(pin(page)))
    assert page.errors == []
