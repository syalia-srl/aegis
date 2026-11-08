"""The mount kernel in a foreign page (plan 2026-11-08, slice a2): one
``.aegis`` root, one websocket shared by every mount, and a destroy that gives
both back. Headless Chromium against the real ``aegis serve`` the fixture
starts, at desktop and phone width.

The page is the stranger's, not the standalone client: a board of its own and
two empty divs to mount into. Nothing here asserts styling (a3 rewrites the
stylesheets); it asserts the DOM boundary, the socket count and the welcome.
"""

import json
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from .test_browser import browser  # noqa: F401

pytest.importorskip("playwright.sync_api")

pytestmark = [pytest.mark.browser, pytest.mark.slow]

ROOT = Path(__file__).parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def host(tmp_path: Path, fake_claude: str):
    """The fixture host page, ready, as ``{"url", "session"}``."""
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.host_fixture",
            "--root",
            str(tmp_path),
            "--port",
            str(port),
            "--claude",
            fake_claude,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # The server logs to this pipe too (a cgroup warning, for one); the
        # ready line is the JSON one.
        info = None
        noise: list[str] = []
        deadline = time.monotonic() + 120
        while info is None:
            wait = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([proc.stdout], [], [], wait)
            line = proc.stdout.readline() if ready else ""
            if not line:
                raise AssertionError(
                    "the host fixture printed no ready line:\n" + "".join(noise)
                )
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                noise.append(line)
        # Read the rest into a thread: a pipe nobody reads fills at 64 KB.
        drained: list[str] = noise
        threading.Thread(
            target=drained.extend, args=(proc.stdout,), daemon=True
        ).start()
        yield info | {"root": str(tmp_path)}
        assert proc.poll() is None, "".join(drained)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=15)
        proc.stdout.close()


def open_page(pw, width: int):
    page = pw.new_page(viewport={"width": width, "height": 800})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "console",
        lambda m: m.type == "error" and errors.append(m.text),
    )
    page.on("dialog", lambda d: d.accept())
    return page, errors


def mounted(page) -> None:
    """The root is built, the welcome arrived, and the mount holds the
    sessions snapshot (a turn driven before it lands would be one the mount
    joined halfway)."""
    # The host decides how tall a chat is (the plan's styling contract); the
    # fixture page itself gains this in a8, and this slice needs a box to fit.
    page.add_style_tag(content="#chat { height: 700px; }")
    page.wait_for_function(
        "() => document.querySelector('#chat > .aegis')"
        " && window.__wsMsgs.some((m) => m.t === 'welcome')"
        " && window.__wsMsgs.some((m) => m.t === 'snapshot' && m.channel === 'sessions')",
        timeout=30_000,
    )


SEND = """async ([sid, text]) => {
  const { PROTO } = await import(`${window.aegisHost.prefix}/static/js/protocol.js`);
  const url = location.origin.replace(/^http/, "ws") + window.aegisHost.prefix + "/ws";
  const ws = new WebSocket(url);
  await new Promise((ok, no) => {
    ws.addEventListener("open", ok, { once: true });
    ws.addEventListener("error", no, { once: true });
  });
  const reply = new Promise((ok) => {
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.t === "welcome") {
        ws.send(JSON.stringify({ t: "call", id: 1, op: "session.send",
          params: { log_id: sid, text } }));
      }
      if (m.t === "reply" && m.id === 1) {
        ws.close();
        ok(m);
      }
    });
  });
  ws.send(JSON.stringify({ t: "hello", proto: PROTO, token: window.aegisHost.token }));
  const got = await reply;
  if (got.error) throw new Error(JSON.stringify(got.error));
}"""


def send(page, sid: str, text: str) -> None:
    """A message into the fixture's session, over the same protocol a page
    uses, so the turn reaches the mount on its own subscription."""
    page.evaluate(SEND, [sid, text])


@pytest.mark.parametrize("width", [1280, 390])
def test_a_mount_is_one_root_and_one_socket(host, browser, width):  # noqa: F811
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    assert page.locator("#chat > .aegis").count() == 1
    assert page.get_attribute("#chat > .aegis", "data-theme") == "host"
    assert page.evaluate("window.__ws.length") == 1
    assert page.evaluate("window.__ws[0].closed") is False
    welcome = page.evaluate("window.__wsMsgs.find((m) => m.t === 'welcome')")
    assert welcome["me"]["name"] == "ana"
    # The root holds the layout's own element; a2 composes nothing else yet.
    assert page.locator("#chat .aegis > .aegis-chat").count() == 1
    # The host's page is its own: only #chat gained anything.
    assert page.evaluate("document.getElementById('board').outerHTML") == (
        '<main id="board">A host page</main>'
    )
    assert page.inner_text("#board") == "A host page"
    assert page.evaluate("document.getElementById('chat2').children.length") == 0
    assert errors == []
    page.close()


def test_a_second_mount_shares_the_socket_and_destroy_lets_both_go(host, browser):  # noqa: F811
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__chat2 = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
          });
        }""",
        prefix,
    )
    page.wait_for_function("() => document.querySelector('#chat2 > .aegis')")
    # Same URL and token: the second mount subscribes on the first's socket.
    assert page.evaluate("window.__ws.length") == 1
    assert page.locator("#chat .aegis-chat").count() == 1
    assert page.locator("#chat2 .aegis-chat").count() == 1
    page.evaluate("window.__chat.destroy()")
    assert page.locator("#chat > .aegis").count() == 0
    assert page.evaluate("window.__ws[0].closed") is False  # chat2 still holds it
    page.evaluate("window.__chat2.destroy()")
    page.wait_for_function("() => window.__ws[0].closed === true", timeout=10_000)
    assert page.locator("#chat2 > .aegis").count() == 0
    assert errors == []
    page.close()


def test_turn_end_fires_once_for_the_mounts_own_session(host, browser):  # noqa: F811
    """A chat reports the turn of the session it follows, and a second mount
    following another session on the same socket stays silent."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__turns = [];
          window.__turns2 = [];
          window.__chat.on("turnEnd", (t) => window.__turns.push(t));
          window.__chat2 = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session2,
            layout: "chat",
            theme: "host",
          });
          window.__chat2.on("turnEnd", (t) => window.__turns2.push(t));
        }""",
        prefix,
    )
    page.wait_for_function("() => document.querySelector('#chat2 > .aegis')")
    # One turn of Ana's session, sent the way a page sends one: a call over
    # the same protocol and token. The reply only says the message was
    # delivered; the turn's end arrives on the sessions channel.
    page.evaluate(
        """async ([sid, text]) => {
          const { PROTO } = await import(`${window.aegisHost.prefix}/static/js/protocol.js`);
          const url =
            location.origin.replace(/^http/, "ws") + window.aegisHost.prefix + "/ws";
          const ws = new WebSocket(url);
          await new Promise((ok, no) => {
            ws.addEventListener("open", ok, { once: true });
            ws.addEventListener("error", no, { once: true });
          });
          const reply = new Promise((ok) => {
            ws.addEventListener("message", (ev) => {
              const m = JSON.parse(ev.data);
              if (m.t === "welcome") {
                ws.send(
                  JSON.stringify({
                    t: "call",
                    id: 1,
                    op: "session.send",
                    params: { log_id: sid, text },
                  }),
                );
              }
              if (m.t === "reply" && m.id === 1) {
                ws.close();
                ok(m);
              }
            });
          });
          ws.send(
            JSON.stringify({ t: "hello", proto: PROTO, token: window.aegisHost.token }),
          );
          const got = await reply;
          if (got.error) throw new Error(JSON.stringify(got.error));
        }""",
        [host["session"], "hello from the turnEnd test"],
    )
    page.wait_for_function("() => window.__turns.length > 0", timeout=60_000)
    page.wait_for_timeout(1500)  # a turnEnd the filter failed to stop lands here
    turns = page.evaluate("window.__turns")
    assert len(turns) == 1
    assert turns[0]["session"] == host["session"]
    assert isinstance(turns[0]["cost_usd"], (int, float))
    assert turns[0]["cost_usd"] > 0
    assert page.evaluate("window.__turns2.length") == 0
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_the_chat_draws_rows_a_tool_opens_and_entries_are_reported(
    host,
    browser,  # noqa: F811
    width,
):
    """A chat following one session draws what the server sends: two turns'
    prose as two rows, a tool row that opens, and one entry event per upsert."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    # The chat fills the element the host gave it; the transcript fills the chat.
    assert (
        page.evaluate(
            "document.querySelector('#chat .aegis').getBoundingClientRect().height"
        )
        == 700
    )
    assert (
        page.evaluate(
            "document.querySelector('#chat .tr-pane').getBoundingClientRect().height"
        )
        > 500
    )
    page.evaluate("""() => {
      window.__entries = [];
      window.__chat.on("entry", (e) => window.__entries.push(e));
    }""")
    send(page, host["session"], "first message to the chat")
    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat .aegis #entries .row.prose .body")]
          .some((b) => b.textContent.includes("first message to the chat"))""",
        timeout=60_000,
    )
    send(page, host["session"], "second message to the chat")
    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat .aegis #entries .row.prose .body")]
          .some((b) => b.textContent.includes("second message to the chat"))""",
        timeout=60_000,
    )
    rows = page.locator("#chat .aegis #entries .row.prose .body").all_inner_texts()
    assert len([r for r in rows if "message to the chat" in r]) == 2

    send(page, host["session"], "/many 3")
    tool = page.locator("#chat .aegis #entries .row.tool").first
    tool.wait_for(timeout=60_000)
    tool.locator("details > summary").click()
    assert tool.locator("details").get_attribute("open") is not None

    page.wait_for_function("() => window.__entries.length > 0", timeout=60_000)
    got = page.evaluate("window.__entries")
    assert all(e["session"] == host["session"] for e in got)
    assert any(e["entry"]["kind"] == "prose" for e in got)
    assert any(e["entry"]["kind"] == "tool" for e in got)
    assert errors == []
    page.close()


def test_a_sent_file_opens_in_the_mounts_viewer(host, browser):  # noqa: F811
    """A file_send's card opens in the part's own viewer, under #chat, and the
    file is fetched through the host's prefix."""
    note = Path(host["root"]) / "host-note.txt"
    note.write_text("a note sent from the host page\n")
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    send(
        page,
        host["session"],
        "/mcp file_send "
        + json.dumps({"paths": [str(note)], "caption": "from the test"}),
    )
    row = page.locator("#chat .aegis #entries .row.file")
    row.wait_for(timeout=60_000)
    row.locator(".btn.view").click()
    viewer = page.locator("#chat .aegis .viewer")
    viewer.wait_for(state="visible", timeout=10_000)
    page.wait_for_function(
        """() => document.querySelector("#chat .aegis .viewer .vbody")?.textContent
          .includes("a note sent from the host page")""",
        timeout=30_000,
    )
    # A second mount's viewer would be another .viewer; this one belongs to the
    # part's own root and the host's page holds no #a2.
    assert page.locator("#chat .aegis .viewer").count() == 1
    assert page.evaluate("document.getElementById('a2')") is None
    assert errors == []
    page.close()


def test_scrolling_up_mounts_the_rows_above_the_window(host, browser):  # noqa: F811
    """The transcript keeps a window of rows; a scroll to the top mounts the
    entries the snapshot left above it."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    send(page, host["session"], "/many 205")
    page.wait_for_function(
        '() => document.querySelectorAll("#chat .aegis #entries > .row").length >= 200',
        timeout=120_000,
    )
    at_bottom = page.evaluate(
        'document.querySelectorAll("#chat .aegis #entries > .row").length'
    )
    assert 200 <= at_bottom < 205  # a window, not the whole transcript
    page.evaluate("""() => {
      const tr = document.querySelector("#chat .aegis .tr");
      tr.scrollTop = 0;
      tr.dispatchEvent(new Event("scroll"));
    }""")
    page.wait_for_function(
        f'() => document.querySelectorAll("#chat .aegis #entries > .row").length > {at_bottom}',
        timeout=30_000,
    )
    assert (
        page.evaluate(
            'document.querySelectorAll("#chat .aegis #entries > .row").length'
        )
        > at_bottom
    )
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_a_spanish_mount_reads_spanish_and_the_english_one_stays_english(
    host,
    browser,  # noqa: F811
    width,
):
    """Two mounts on one page keep their own tables (a5): the Spanish chat's
    chrome and the rows it draws are Spanish, and the first chat is untouched."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__es = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            lang: "es",
          });
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #find-q')", timeout=30_000
    )

    # The chrome: placeholders, titles and the find bar's own words.
    assert page.get_attribute("#chat2 .aegis #find-q", "placeholder") == (
        "Buscar en la transcripción"
    )
    assert page.get_attribute("#chat2 .aegis #nav-up", "title") == (
        "Mensaje anterior del agente (Alt+↑)"
    )
    assert page.get_attribute("#chat2 .aegis #nav-recap", "aria-label") == (
        "Resumir dónde va esta sesión"
    )
    # The English mount beside it still reads its own table.
    assert page.get_attribute("#chat .aegis #find-q", "placeholder") == (
        "Find in transcript"
    )

    # A row drawn from the server's data carries Spanish actions too: the
    # copy button above the person's message is one of them.
    send(page, host["session"], "a message the Spanish chat draws")
    copy = page.locator("#chat2 .aegis #entries .row.user .body > .copy").first
    copy.wait_for(timeout=60_000)
    assert copy.get_attribute("title") == "Copiar el mensaje"
    assert page.get_attribute("#chat2 .aegis #nav-fold", "title") is not None
    assert page.get_attribute("#chat2 .aegis #nav-fold", "title").startswith(
        "Todo a la vista"
    )
    assert errors == []
    page.close()


def test_a_missing_label_falls_back_to_english_and_warns_once(host, browser):  # noqa: F811
    """A key the table lacks falls back to English and names itself once (a5);
    a language with no table at all reads English, and the Spanish one is
    Spanish."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    got = page.evaluate(
        """async (prefix) => {
          const { labelsFor } = await import(`${prefix}/static/js/labels.js`);
          const seen = [];
          const warn = console.warn;
          console.warn = (...a) => seen.push(a.join(" "));
          const es = labelsFor("es", {});
          const missing = es("no.such.key");
          const again = es("no.such.key");
          console.warn = warn;
          return {
            missing,
            again,
            warnings: seen.filter((w) => w.includes("no.such.key")),
            french: labelsFor("fr", {})("toast.dismiss"),
            spanish: es("toast.dismiss"),
          };
        }""",
        prefix,
    )
    assert got["missing"] == "no.such.key"  # absent everywhere: the key itself
    assert got["again"] == "no.such.key"
    assert len(got["warnings"]) == 1  # logged by name once, not per call
    assert got["french"] == "Dismiss"  # no French table: English
    assert got["spanish"] == "Descartar"
    assert errors == []
    page.close()
