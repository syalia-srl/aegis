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


@pytest.mark.parametrize("width", [1280, 390])
def test_a_spanish_composer_reads_spanish(host, browser, width):  # noqa: F811
    """a7: the mount's box takes its placeholder and button titles from the
    Spanish table, while the English mount beside it keeps its own (a7)."""
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
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )

    assert page.get_attribute("#chat2 .aegis #input", "placeholder") == (
        "Escríbele al agente. Enter envía, Shift+Enter agrega una línea, "
        "/ para comandos, Esc interrumpe."
    )
    assert page.get_attribute("#chat2 .aegis #send", "title") == "Enviar (Enter)"
    assert page.get_attribute("#chat2 .aegis #send", "aria-label") == "Enviar"
    assert page.get_attribute("#chat2 .aegis #attach", "title") == ("Adjuntar archivos")
    assert page.get_attribute("#chat2 .aegis #interrupt", "title") == (
        "Interrumpir (Esc)"
    )
    # The English mount beside it still reads its own table.
    assert page.get_attribute("#chat .aegis #send", "title") == "Send (Enter)"
    assert page.get_attribute("#chat .aegis #attach", "title") == "Attach files"
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


# -- the composer part (a6) --------------------------------------------------
# These drive the box the mount built, never the page's: the chat's #input is
# looked up under #chat, as a host's own ids would be.


def composer_send(page, text: str) -> None:
    """Type into the mount's box and press Enter, as a person would."""
    box = page.locator("#chat .aegis #input")
    box.click()
    box.fill(text)
    box.press("Enter")


@pytest.mark.parametrize("width", [1280, 390])
def test_the_composer_sends_from_the_mount_and_the_reply_streams(
    host,
    browser,  # noqa: F811
    width,
):
    """The box the mount owns sends through the mount's socket: the person's
    message draws as a row and the agent's answer streams back after it."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    assert page.locator("#chat .aegis #composer").count() == 1
    assert page.locator("#chat .aegis #composer #input").count() == 1
    # The chips name the session the chat follows.
    assert "opus" in page.inner_text("#chat .aegis #chip-model")
    composer_send(page, "hello from the mount's own box")
    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat .aegis #entries .row.user .body")]
          .some((b) => b.textContent.includes("hello from the mount's own box"))""",
        timeout=60_000,
    )
    # The agent answered after it, in the same transcript.
    page.wait_for_function(
        """() => {
          const rows = [...document.querySelectorAll("#chat .aegis #entries > .row")];
          const at = rows.findIndex((r) => r.textContent.includes("hello from the mount's own box"));
          return rows.slice(at + 1).some((r) => r.classList.contains("prose") && r.textContent.trim());
        }""",
        timeout=60_000,
    )
    # The box is empty and free again, and the draft is gone from the device.
    assert page.input_value("#chat .aegis #input") == ""
    assert (
        page.evaluate("localStorage.getItem('aegis.draft.' + window.aegisHost.session)")
        is None
    )
    assert errors == []
    page.close()


def test_the_controls_option_hides_the_composer_chips_at_phone_width(host, browser):  # noqa: F811
    """A product chat hides the model, effort and permission chips (#994);
    the mount beside it keeps the default."""
    page, errors = open_page(browser, 390)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__nochips = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            controls: false,
          });
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )
    for chip in ("chip-model", "chip-effort", "chip-perm"):
        assert page.locator(f"#chat .aegis #{chip}").is_visible()
        assert not page.locator(f"#chat2 .aegis #{chip}").is_visible()
    assert page.locator("#chat .aegis #composer").count() == 1
    assert page.locator("#chat2 .aegis #composer").count() == 1
    assert errors == []
    page.close()


def test_an_attachment_uploads_as_a_chip_and_goes_out_with_the_message(host, browser):  # noqa: F811
    """A file chosen on the mount's paperclip uploads into the session as a
    chip, and the send names it: the row that lands carries its card."""
    note = Path(host["root"]) / "attached-note.txt"
    note.write_text("a note the person attached\n")
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    page.set_input_files("#chat .aegis #attach-pick", str(note))
    chip = page.locator("#chat .aegis #atts .chip.att")
    chip.wait_for(timeout=30_000)
    assert "attached-note.txt" in chip.inner_text()
    page.wait_for_selector("#chat .aegis #atts .chip.att.done", timeout=30_000)
    page.fill("#chat .aegis #input", "with a file")
    page.press("#chat .aegis #input", "Enter")
    card = page.locator("#chat .aegis #entries .row.user .fcard.att")
    card.first.wait_for(timeout=60_000)
    assert "attached-note.txt" in page.inner_text("#chat .aegis #entries")
    # The tray is empty again once the send took the file.
    page.wait_for_selector("#chat .aegis #atts", state="hidden", timeout=10_000)
    assert errors == []
    page.close()


def test_the_interrupt_button_stops_a_working_turn(host, browser):  # noqa: F811
    """The mount's own interrupt sends session.interrupt for the session it
    follows, and the turn ends."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    assert page.is_hidden("#chat .aegis #interrupt")
    composer_send(page, "/busy 5")
    page.wait_for_selector("#chat .aegis #interrupt", state="visible", timeout=60_000)
    page.click("#chat .aegis #interrupt")
    page.wait_for_selector("#chat .aegis #interrupt", state="hidden", timeout=30_000)
    page.wait_for_function(
        """() => /interrupted|done in/.test(document.querySelector("#chat .aegis #entries").textContent)""",
        timeout=30_000,
    )
    assert errors == []
    page.close()


# -- the command menu and dictation (b4) --------------------------------------
# Both are optional parts of the composer: a chat that did not ask for them has
# empty slots, and one that did builds them under its own element.


def test_the_menu_and_mic_are_opt_in_and_built_under_their_slots(host, browser):  # noqa: F811
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    # The default chat asked for neither: the slots stay empty.
    assert page.locator("#chat .aegis [data-slot=menu] #cmd-menu").count() == 0
    assert page.locator("#chat .aegis [data-slot=mic] button").count() == 0
    prefix = page.evaluate("window.aegisHost.prefix")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__chat2 = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            commands: true,
            dictation: true,
          });
        }""",
        prefix,
    )
    # Wait for the mount's session meta: the menu asks for its catalog by key.
    page.wait_for_function(
        """() => {
          const chip = document.querySelector("#chat2 .aegis #chip-model");
          return chip && chip.textContent.includes("opus");
        }""",
        timeout=30_000,
    )
    # Typing "/" opens the list under the composer's own slot.
    box = page.locator("#chat2 .aegis #input")
    box.click()
    box.fill("/")
    page.wait_for_selector(
        "#chat2 .aegis [data-slot=menu] #cmd-menu .cmd-row", timeout=30_000
    )
    # Escape closes it and leaves the line as typed.
    box.press("Escape")
    page.wait_for_selector("#chat2 .aegis #cmd-menu", state="hidden", timeout=10_000)
    # The mic is the slot's button, idle, and knows it can record a take.
    mic = page.locator("#chat2 .aegis [data-slot=mic] button.mic")
    assert mic.count() == 1
    assert mic.get_attribute("data-state") == "idle"
    assert "hold to record" in (mic.get_attribute("title") or "")
    assert errors == []
    page.close()


OFFLINE = """async (prefix) => {
  // The same socket the mount holds (the registry is keyed by URL and token),
  // dropped as a stalled one is; every reconnect from here meets an inert
  // WebSocket, so the box is offline until the test says otherwise.
  const { acquire } = await import(`${prefix}/static/js/connection.js`);
  // mount.js builds new URL("ws", <prefix>/): an http address, key for key.
  const url = location.origin + prefix + "/ws";
  window.__held = acquire(url, { token: window.aegisHost.token });
  window.__liveWS = window.WebSocket;
  window.__held.conn.drop();
  window.WebSocket = class InertWebSocket {
    constructor(url) { this.url = url; }
    send() {}
    close() {}
  };
  return true;
}"""

ONLINE = """async () => {
  window.WebSocket = window.__liveWS;
  window.__held.conn.connect(); // what a reconnect does: hello, welcome, a flush
  return true;
}"""


def test_a_message_sent_offline_is_queued_and_goes_out_once_on_reconnect(host, browser):  # noqa: F811
    """The mount's outbox: with no connection a send waits as the device's
    draft and draws as the transcript's queued row; the reconnect flushes it,
    and the agent sees it once."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.evaluate(OFFLINE, prefix)
    page.wait_for_timeout(800)  # the retry meets the inert socket
    composer_send(page, "ship it while offline")
    queued = page.locator("#chat .aegis #queued")
    queued.wait_for(state="visible", timeout=10_000)
    assert "ship it while offline" in queued.inner_text()
    assert page.input_value("#chat .aegis #input") == ""
    # The draft is the device's, keyed by the session.
    stored = page.evaluate(
        """prefix => import(`${prefix}/static/js/devicestore.js`).then(s => s.outboxAll())
        .then(all => Object.fromEntries(all))""",
        prefix,
    )
    assert stored[host["session"]]["text"] == "ship it while offline"
    page.evaluate(ONLINE)
    queued.wait_for(state="hidden", timeout=30_000)
    page.wait_for_function(
        """t => [...document.querySelectorAll("#chat .aegis #entries .row.user .body")]
          .some((b) => b.textContent.includes(t))""",
        arg="ship it while offline",
        timeout=60_000,
    )
    # Once: a second copy would be a second row.
    said = page.evaluate(
        """t => [...document.querySelectorAll("#chat .aegis #entries .row.user")]
          .filter((r) => r.textContent.includes(t)).length""",
        arg="ship it while offline",
    )
    assert said == 1
    assert errors == []
    page.close()


# -- the whole chat in a stranger's page (a8) --------------------------------
# The page was already using the ids and the CSS a chat would trip over: its
# own #tabs and #composer, a #input of its own to come, and a stylesheet that
# names every variable plus a rule that hides tool calls. The mount may not
# move, rename or restyle any of it.

# The host's own boxes, the same shape the fixture recorded before the mount.
HOST_BOXES = """() => {
  const out = {};
  for (const id of ["board", "tabs", "composer"]) {
    const n = document.getElementById(id);
    const r = n.getBoundingClientRect();
    out[id] = {text: n.textContent, x: r.x, y: r.y, width: r.width, height: r.height};
  }
  return out;
}"""

# The session's cumulative cost_usd as the sessions channel carried it, replayed
# from the page's own socket log: what the mount's turnEnd has to agree with.
COST = """(sid) => {
  let cost = 0;
  for (const m of window.__wsMsgs) {
    if (m.channel !== "sessions") continue;
    if (m.t === "snapshot") {
      const row = (m.data || []).find((r) => r.log_id === sid);
      if (row && typeof row.cost_usd === "number") cost = row.cost_usd;
    } else if (m.t === "patch") {
      for (const op of m.ops || []) {
        const row = op.merge || op.upsert;
        if (row && row.log_id === sid && typeof row.cost_usd === "number") cost = row.cost_usd;
      }
    }
  }
  return cost;
}"""


@pytest.mark.parametrize("width", [1280, 390])
def test_the_whole_chat_in_a_page_that_already_had_the_ids(
    host,
    browser,  # noqa: F811
    width,
):
    """The phase-A story on the fixture page: the host's boxes and ids survive,
    the mount paints in the host's colours, the host's rule hides tool calls, a
    send from the mount streams back and reports one turn with the turn's own
    cost, and a send while the socket is down waits and goes out once."""
    page, errors = open_page(browser, width)
    page.goto(host["url"] + "?tools=hidden")
    mounted(page)

    # The host's own boxes stand exactly where they stood before the mount.
    assert page.evaluate(HOST_BOXES) == page.evaluate("window.__host")
    assert page.evaluate("document.getElementById('composer').textContent") == (
        "the host's own input"
    )
    assert page.evaluate("document.getElementById('tabs').textContent") == (
        "the host's own tabs"
    )
    # An id aegis also uses still returns the host's node, never the mount's.
    assert (
        page.evaluate("document.getElementById('composer').closest('.aegis')") is None
    )
    assert page.evaluate("document.getElementById('tabs').closest('.aegis')") is None
    # The mount's composer is under the mount, and the page's only #input.
    assert page.locator("#chat .aegis #composer #input").count() == 1
    assert page.evaluate("document.querySelectorAll('#input').length") == 1

    # theme: "host" reads the host's variables: its --bg is what the mount
    # paints, resolved by the browser rather than compared as a string.
    colour = page.evaluate(
        """() => {
          const probe = document.createElement("div");
          probe.style.background = "var(--bg)";
          document.body.append(probe);
          const want = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return {
            want,
            got: getComputedStyle(document.querySelector("#chat .aegis")).backgroundColor,
            declared: getComputedStyle(document.documentElement)
              .getPropertyValue("--bg").trim(),
          };
        }"""
    )
    assert colour["declared"] == "#fbf8f2"
    assert colour["got"] == colour["want"] != "rgba(0, 0, 0, 0)"

    page.evaluate(
        """() => {
          window.__turns = [];
          window.__chat.on("turnEnd", (t) => window.__turns.push(t));
        }"""
    )

    # With the socket down the same box queues the message on the device and
    # draws the transcript's queued row; the reconnect delivers it once. It is
    # the first message this session gets, so the draft's revision cannot have
    # been passed by a row the page had not seen yet.
    prefix = page.evaluate("window.aegisHost.prefix")
    page.evaluate(OFFLINE, prefix)
    page.wait_for_timeout(800)  # the retry meets the inert socket
    composer_send(page, "held while the host was down")
    queued = page.locator("#chat .aegis #queued")
    queued.wait_for(state="visible", timeout=10_000)
    assert "held while the host was down" in queued.inner_text()
    page.evaluate(ONLINE)
    queued.wait_for(state="hidden", timeout=30_000)
    page.wait_for_function(
        """t => [...document.querySelectorAll("#chat .aegis #entries .row.user .body")]
          .some((b) => b.textContent.includes(t))""",
        arg="held while the host was down",
        timeout=60_000,
    )
    said = page.evaluate(
        """t => [...document.querySelectorAll("#chat .aegis #entries .row.user")]
          .filter((r) => r.textContent.includes(t)).length""",
        arg="held while the host was down",
    )
    assert said == 1
    page.wait_for_function("() => window.__turns.length >= 1", timeout=60_000)

    # A prose row draws as the host's, and a tool row is in the DOM but the
    # host's own rule keeps it off the page.
    assert page.locator("#chat .aegis #entries .row.prose").first.is_visible()
    send(page, host["session"], "/many 3")
    tool = page.locator("#chat .aegis #entries .row.tool").first
    tool.wait_for(state="attached", timeout=60_000)
    assert tool.is_hidden()
    page.wait_for_function("() => window.__turns.length >= 2", timeout=60_000)

    # A message typed into the mount's box streams back, and its turn ends
    # once: the cost is this turn's own, the cumulative figure after minus the
    # one before, and a plain done turn carries no line.
    seen = page.evaluate("window.__turns.length")
    before = page.evaluate(COST, host["session"])
    composer_send(page, "typed in the host's page")
    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat .aegis #entries .row.user .body")]
          .some((b) => b.textContent.includes("typed in the host's page"))""",
        timeout=60_000,
    )
    page.wait_for_function(f"() => window.__turns.length > {seen}", timeout=60_000)
    page.wait_for_timeout(1500)  # a second turnEnd for the same turn lands here
    turns = page.evaluate("window.__turns")
    assert len(turns) == seen + 1
    turn = turns[seen]
    assert turn["session"] == host["session"]
    assert turn["line"] is None
    assert isinstance(turn["cost_usd"], (int, float))
    assert turn["cost_usd"] > 0
    after = page.evaluate(COST, host["session"])
    assert turn["cost_usd"] == pytest.approx(after - before, abs=1e-9)

    assert errors == []
    page.close()


# -- the host's calls and the missing events (m1, #962) -----------------------
# A chat host can change the session it shows, start one, put the caret in the
# box, and hear a turn start and a file arrive. These are additive: every case
# above them is the regression, and Oikos's exact options get their old
# payloads unchanged.


@pytest.mark.parametrize("width", [1280, 390])
def test_open_and_focus_are_the_hosts_calls(host, browser, width):  # noqa: F811
    """open(session) moves the transcript and box and reports the change once;
    focus() puts the caret in this mount's own box, or in its transcript when it
    has no box."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    page.evaluate("""() => {
      window.__changes = [];
      window.__chat.on("sessionChanged", (e) => window.__changes.push(e));
    }""")
    assert page.evaluate("window.__chat.focus()") is None
    assert page.evaluate(
        "document.activeElement === document.querySelector('#chat .aegis #input')"
    )
    # A chat that started on one session reports the next one it shows, with
    # the one it left, and open() stays chainable.
    assert page.evaluate(
        "window.__chat.open(window.aegisHost.session2) === window.__chat"
    )
    page.wait_for_function("() => window.__changes.length === 1", timeout=10_000)
    change = page.evaluate("window.__changes[0]")
    assert change["session"] == host["session2"]
    assert change["previous"] == host["session"]
    assert change["meta"]["key"] == host["session2"]
    # The transcript is the new session's: a message typed now draws there.
    composer_send(page, "a message for the second session")
    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat .aegis #entries .row.user .body")]
          .some((b) => b.textContent.includes("a message for the second session"))""",
        timeout=60_000,
    )

    # A transcript-only mount focuses its .tr, which has tabindex="-1".
    prefix = page.evaluate("window.aegisHost.prefix")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__bare = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            composer: false,
          });
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis .tr')", timeout=30_000
    )
    assert page.evaluate("window.__bare.focus()") is None
    assert page.evaluate(
        "document.activeElement === document.querySelector('#chat2 .aegis .tr')"
    )
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_a_mount_with_an_agent_spawns_on_its_first_send(host, browser, width):  # noqa: F811
    """No session and an agent: the first send starts a session, opens it (one
    sessionChanged with previous null), and the turn reports turnStart then
    turnEnd with its own cost."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__fresh = {changed: [], starts: [], ends: [], entries: []};
          window.__agent = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            agent: "opus",
            layout: "chat",
            theme: "host",
          });
          window.__agent.on("sessionChanged", (e) => window.__fresh.changed.push(e));
          window.__agent.on("turnStart", (e) => window.__fresh.starts.push(e));
          window.__agent.on("turnEnd", (e) => window.__fresh.ends.push(e));
          window.__agent.on("entry", (e) => window.__fresh.entries.push(e));
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )
    # Nothing was invented before the first message.
    assert page.evaluate("window.__fresh.changed.length") == 0
    assert page.evaluate("window.__fresh.starts.length") == 0

    page.evaluate("() => window.__agent.send('hello from a fresh chat')")
    page.wait_for_function("() => window.__fresh.changed.length === 1", timeout=60_000)
    change = page.evaluate("window.__fresh.changed[0]")
    assert change["previous"] is None
    assert change["session"]
    assert change["meta"]["key"] == change["session"]

    page.wait_for_function("() => window.__fresh.starts.length === 1", timeout=60_000)
    start = page.evaluate("window.__fresh.starts[0]")
    assert start["session"] == change["session"]
    assert start["meta"]["state"] == "working"

    page.wait_for_function("() => window.__fresh.ends.length === 1", timeout=60_000)
    end = page.evaluate("window.__fresh.ends[0]")
    assert end["session"] == change["session"]
    assert isinstance(end["cost_usd"], (int, float))
    assert end["cost_usd"] > 0
    assert end["line"] is None

    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat2 .aegis #entries .row.user .body")]
          .some((b) => b.textContent.includes("hello from a fresh chat"))""",
        timeout=60_000,
    )
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_session_changed_is_silent_for_the_constructor_session(host, browser, width):  # noqa: F811
    """A Mount built directly can register its listener before start(): the
    constructor's session is followed silently, and a later open() reports it."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { Mount } = await import(`${prefix}/static/js/mount.js`);
          window.__ev = [];
          const m = new Mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
          });
          m.on("sessionChanged", (e) => window.__ev.push(e));
          window.__direct = m;
          m.start();
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )
    page.wait_for_timeout(500)  # a first follow that reported lands here
    assert page.evaluate("window.__ev.length") == 0
    page.evaluate("s => window.__direct.open(s)", host["session2"])
    page.wait_for_function("() => window.__ev.length === 1", timeout=10_000)
    ev = page.evaluate("window.__ev[0]")
    assert ev["session"] == host["session2"]
    assert ev["previous"] == host["session"]
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_file_sent_event_reports_its_entry_and_files(host, browser, width):  # noqa: F811
    """A file card's upsert fires fileSent beside entry, with the card's files
    and the same entry the generic event carried."""
    note = Path(host["root"]) / "event-note.txt"
    note.write_text("a file the host hears about\n")
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    page.evaluate("""() => {
      window.__files = [];
      window.__entries = [];
      window.__chat.on("fileSent", (e) => window.__files.push(e));
      window.__chat.on("entry", (e) => window.__entries.push(e));
    }""")
    send(
        page,
        host["session"],
        "/mcp file_send "
        + json.dumps({"paths": [str(note)], "caption": "a file the host hears"}),
    )
    page.wait_for_function("() => window.__files.length > 0", timeout=60_000)
    file_event = page.evaluate("window.__files[0]")
    assert file_event["session"] == host["session"]
    assert file_event["entry"]["kind"] == "file"
    assert len(file_event["files"]) == 1
    entries = page.evaluate("window.__entries")
    same = [
        e
        for e in entries
        if e["entry"] == file_event["entry"] and e["session"] == host["session"]
    ]
    assert len(same) == 1  # the entry event carried the same object
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_oikos_options_still_get_their_events(host, browser, width):  # noqa: F811
    """Oikos's exact options (a token function, chat, host theme, Spanish, no
    controls) still get connection, error and turnEnd with their old payloads."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__oikos = {conn: [], errs: [], turns: []};
          window.__o = mount(document.getElementById("chat2"), {
            token: () => window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            lang: "es",
            controls: false,
          });
          window.__o.on("connection", (e) => window.__oikos.conn.push(e));
          window.__o.on("error", (e) => window.__oikos.errs.push(e));
          window.__o.on("turnEnd", (e) => window.__oikos.turns.push(e));
        }""",
        prefix,
    )
    page.wait_for_function("() => window.__oikos.conn.length > 0", timeout=30_000)
    conn = page.evaluate("window.__oikos.conn[0]")
    assert set(conn) == {"state", "server"}
    assert conn["state"] in {"connecting", "open"}

    # A refused call reaches the host as its code and message, and the error.
    page.evaluate("() => { window.__o.spawn('no-such-agent').catch(() => {}); }")
    page.wait_for_function("() => window.__oikos.errs.length > 0", timeout=30_000)
    err = page.evaluate("window.__oikos.errs[0]")
    assert set(err) == {"session", "code", "message", "error"}
    assert err["session"] is None
    assert isinstance(err["code"], str) and err["code"]
    assert isinstance(err["message"], str)

    box = page.locator("#chat2 .aegis #input")
    box.click()
    box.fill("hola desde oikos")
    box.press("Enter")
    page.wait_for_function("() => window.__oikos.turns.length > 0", timeout=60_000)
    turn = page.evaluate("window.__oikos.turns[0]")
    assert set(turn) == {"session", "cost_usd", "line", "meta"}
    assert turn["session"] == host["session"]
    assert isinstance(turn["cost_usd"], (int, float))
    assert turn["cost_usd"] > 0
    assert errors == []
    page.close()


def test_two_sends_before_the_spawn_answers_start_one_session(host, browser):  # noqa: F811
    """Two send() calls with no session yet share one spawn: one sessionChanged
    and both messages in that one session, never two sessions."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__fresh = {changed: []};
          window.__agent = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            agent: "opus",
            layout: "chat",
            theme: "host",
          });
          window.__agent.on("sessionChanged", (e) => window.__fresh.changed.push(e));
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )
    # Both start before session.spawn has answered.
    page.evaluate(
        """() => {
          const one = window.__agent.send("first before the spawn answers");
          const two = window.__agent.send("second before the spawn answers");
          window.__both = Promise.all([one, two]).catch((e) => e && e.message);
        }"""
    )
    page.wait_for_function("() => window.__fresh.changed.length === 1", timeout=60_000)
    page.wait_for_function(
        """() => {
          const rows = [...document.querySelectorAll("#chat2 .aegis #entries .row.user .body")]
            .map((b) => b.textContent);
          return rows.some((t) => t.includes("first before the spawn answers"))
            && rows.some((t) => t.includes("second before the spawn answers"));
        }""",
        timeout=60_000,
    )
    page.evaluate("() => window.__both")
    assert page.evaluate("window.__fresh.changed.length") == 1
    assert page.evaluate("window.__fresh.changed[0].session")
    assert errors == []
    page.close()


def test_an_empty_send_with_an_agent_starts_nothing(host, browser):  # noqa: F811
    """An empty box sends nothing: with an agent set, the button and a host's
    empty send must not start a session on their own."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__changes = [];
          window.__agent = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            agent: "opus",
            layout: "chat",
            theme: "host",
          });
          window.__agent.on("sessionChanged", (e) => window.__changes.push(e));
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #send')", timeout=30_000
    )
    page.click("#chat2 .aegis #send")  # an empty box's button
    page.evaluate("() => window.__agent.send('   ')")  # a host's empty line
    page.wait_for_timeout(1500)  # a spawn would land here
    assert page.evaluate("window.__changes.length") == 0
    assert page.locator("#chat2 .aegis #input").input_value() == ""
    assert errors == []
    page.close()


def test_file_sent_skips_history_and_fires_once_for_a_live_file(host, browser):  # noqa: F811
    """Opening a session that already holds a file_send replays entry but not
    fileSent; a file sent while the mount watches fires exactly one."""
    old = Path(host["root"]) / "history-note.txt"
    old.write_text("a file from before the mount watched\n")
    new = Path(host["root"]) / "live-note.txt"
    new.write_text("a file the mount sees arrive\n")
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    mounted(page)
    # The second session gets a file while the chat still shows the first.
    send(
        page,
        host["session2"],
        "/mcp file_send " + json.dumps({"paths": [str(old)], "caption": "older file"}),
    )
    # Its turn ends, so the file is in the session's transcript.
    page.wait_for_function(
        """sid => {
          for (const m of window.__wsMsgs) {
            if (m.channel !== "sessions") continue;
            const rows = m.t === "snapshot"
              ? (m.data || [])
              : (m.ops || []).map((o) => o.merge || o.upsert).filter(Boolean);
            const row = rows.find((r) => r && r.log_id === sid);
            if (row && row.state !== "working" && row.cost_usd > 0) return true;
          }
          return false;
        }""",
        arg=host["session2"],
        timeout=60_000,
    )
    page.evaluate(
        """() => {
          window.__files = [];
          window.__entries = [];
          window.__chat.on("fileSent", (e) => window.__files.push(e));
          window.__chat.on("entry", (e) => window.__entries.push(e));
        }"""
    )
    # The history's file draws, but fileSent does not replay it.
    page.evaluate("s => window.__chat.open(s)", host["session2"])
    page.wait_for_selector("#chat .aegis #entries .row.file", timeout=60_000)
    page.wait_for_timeout(1000)
    assert page.evaluate("window.__files.length") == 0
    assert any(e["entry"]["kind"] == "file" for e in page.evaluate("window.__entries"))
    # A new file in the watched session is announced once.
    send(
        page,
        host["session2"],
        "/mcp file_send " + json.dumps({"paths": [str(new)], "caption": "live file"}),
    )
    page.wait_for_function("() => window.__files.length === 1", timeout=60_000)
    page.wait_for_timeout(1500)  # a second upsert would announce here
    assert page.evaluate("window.__files.length") == 1
    event = page.evaluate("window.__files[0]")
    assert event["session"] == host["session2"]
    assert event["entry"]["kind"] == "file"
    assert len(event["files"]) == 1
    assert page.locator("#chat .aegis #entries .row.file").count() >= 2
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_renderers_draw_a_live_tool_card(host, browser, width):  # noqa: F811
    """A host's renderer for a finished tool call replaces aegis's row with
    its own card, keyed by the folded title (``file_send`` for aegis's own
    tool), and it is called again whenever that row upserts."""
    note = Path(host["root"]) / "renderer-note.txt"
    note.write_text("a tool card a host draws\n")
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__drawn = [];
          window.__custom = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            renderers: {
              file_send: (entry, el) => {
                window.__drawn.push({title: entry.title, status: entry.status});
                el.textContent = "life card";
                return true;
              },
            },
          });
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )
    send(
        page,
        host["session"],
        "/mcp file_send "
        + json.dumps({"paths": [str(note)], "caption": "a host's own card"}),
    )
    page.wait_for_function(
        """() => [...document.querySelectorAll("#chat2 .aegis .row.tool .body")]
          .some((b) => b.textContent === "life card")""",
        timeout=60_000,
    )
    drawn = page.evaluate("window.__drawn")
    assert drawn
    assert all(d["title"] == "file_send" for d in drawn)
    assert all(d["status"] != "running" for d in drawn)
    # The custom row is the host's own body under aegis's row shape: none of
    # aegis's details/summary tool drawing survives in it.
    assert page.evaluate(
        """() => {
          const row = [...document.querySelectorAll("#chat2 .aegis .row.tool")]
            .find((r) => r.textContent.includes("life card"));
          return !!row && !row.querySelector("details") && !row.querySelector(".tn");
        }"""
    )
    # The file card the same call sent is still aegis's own: the renderer is
    # for the tool row, not the file board.
    assert page.locator("#chat2 .aegis #entries .row.file").count() >= 1
    assert errors == []
    page.close()


def test_renderers_take_both_key_forms_and_fall_back_on_false_or_throw(host, browser):  # noqa: F811
    """A second MCP server's tool keeps its full harness name as its key; a
    renderer that declines keeps aegis's row, and one that throws leaves the
    row too and does not fail the page (one console line names the tool)."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    page.wait_for_function("() => window.aegisHost", timeout=30_000)
    prefix = page.evaluate("window.aegisHost.prefix")
    got = page.evaluate(
        """async (prefix) => {
          const { render } = await import(`${prefix}/static/js/entries.js`);
          const entry = (title) => ({
            id: `synthetic-${title}`,
            kind: "tool",
            status: "ok",
            ts: Date.now(),
            title,
            summary: "did the thing",
            glyph: "comms",
            detail: {args: "", steps: 0, result: "done"},
          });
          const host = document.createElement("div");
          host.id = "synthetic";
          const custom = render(entry("mcp__life__send_card"), {
            renderers: {
              "mcp__life__send_card": (e, el) => {
                el.textContent = `life ${e.title}`;
                return true;
              },
            },
          });
          const declines = render(entry("file_send"), {
            renderers: {file_send: () => false},
          });
          const throws = render(entry("file_send"), {
            renderers: {file_send: () => { throw new Error("a host bug"); }},
          });
          host.append(custom, declines, throws);
          document.body.append(host);
          return {
            custom: custom.querySelector(".body").textContent,
            declines: !!declines.querySelector("details .line"),
            throws: !!throws.querySelector("details .line"),
            rows: host.querySelectorAll(".row.tool").length,
          };
        }""",
        prefix,
    )
    assert got["custom"] == "life mcp__life__send_card"
    assert got["declines"] is True
    assert got["throws"] is True
    assert got["rows"] == 3
    # The throw is aegis's own log line, not an uncaught page error.
    assert len(errors) == 1
    assert "aegis: renderer file_send" in errors[0]
    assert "a host bug" in errors[0]
    page.close()


def test_transcribe_replaces_the_workers(host, browser):  # noqa: F811
    """A transcribe hook takes each 16 kHz mono piece as a Float32Array and its
    text lands in the box; the model is never loaded and no Worker is made."""
    page, errors = open_page(browser, 1280)
    page.goto(host["url"])
    page.wait_for_function("() => window.aegisHost", timeout=30_000)
    prefix = page.evaluate("window.aegisHost.prefix")
    got = page.evaluate(
        """async (prefix) => {
          const Native = window.Worker;
          window.__workers = 0;
          window.Worker = class extends Native {
            constructor(...args) {
              window.__workers += 1;
              super(...args);
            }
          };
          const { Dictation } = await import(`${prefix}/static/js/dictation.js`);
          const box = document.createElement("textarea");
          document.body.append(box);
          let prepares = 0;
          const seen = {};
          const engine = new Dictation({
            prepare: async () => {
              prepares += 1;
              return {};
            },
            transcribe: async (audio) => {
              seen.type = audio.constructor.name;
              seen.length = audio.length;
              return "spoken words";
            },
          });
          const SR = 16000;
          const source = async (onSamples) => {
            // Nine seconds of voice: the chunker cuts one 4-8 second piece.
            onSamples(new Float32Array(9 * SR).fill(0.5));
            return async () => {};
          };
          const target = {el: box, key: null, current: () => null, heard: () => {}};
          await engine.start(target, source);
          await new Promise((ok, no) => {
            const until = Date.now() + 5000;
            const tick = () => {
              if (box.value) return ok();
              if (Date.now() > until) return no(new Error("no text landed"));
              setTimeout(tick, 25);
            };
            tick();
          });
          return {
            text: box.value,
            type: seen.type,
            length: seen.length,
            prepares,
            workers: window.__workers,
          };
        }""",
        prefix,
    )
    assert got["text"] == "spoken words"
    assert got["type"] == "Float32Array"
    assert 4 * 16000 <= got["length"] <= 8 * 16000
    assert got["prepares"] == 0
    assert got["workers"] == 0
    assert errors == []
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_attachments_false_hides_the_clip_and_ignores_paste(host, browser, width):  # noqa: F811
    """`attachments: false` builds no clip or file input and wires neither
    paste nor drop, while the tray stays for a dictation take; a mount that
    left the default still takes a pasted file."""
    page, errors = open_page(browser, width)
    page.goto(host["url"])
    mounted(page)
    assert page.locator("#chat .aegis #attach").count() == 1
    prefix = page.evaluate("window.aegisHost.prefix")
    page.add_style_tag(content="#chat2 { height: 700px; }")
    page.evaluate(
        """async (prefix) => {
          const { mount } = await import(`${prefix}/aegis.js`);
          window.__noatt = mount(document.getElementById("chat2"), {
            token: window.aegisHost.token,
            session: window.aegisHost.session,
            layout: "chat",
            theme: "host",
            attachments: false,
          });
        }""",
        prefix,
    )
    page.wait_for_function(
        "() => document.querySelector('#chat2 .aegis #input')", timeout=30_000
    )
    assert page.locator("#chat2 .aegis #attach").count() == 0
    assert page.locator("#chat2 .aegis #attach-pick").count() == 0
    assert page.locator("#chat2 .aegis #atts").count() == 1
    # The paste is not the mount's: the page keeps it.
    assert (
        page.evaluate(
            """() => {
              const input = document.querySelector("#chat2 .aegis #input");
              const dt = new DataTransfer();
              dt.items.add(new File(["pasted"], "paste.txt", {type: "text/plain"}));
              const ev = new ClipboardEvent("paste", {
                clipboardData: dt, bubbles: true, cancelable: true,
              });
              input.dispatchEvent(ev);
              return ev.defaultPrevented;
            }"""
        )
        is False
    )
    page.wait_for_timeout(300)
    assert page.locator("#chat2 .aegis #atts .chip").count() == 0
    # A mount that kept the default takes the same paste as a chip.
    assert (
        page.evaluate(
            """() => {
              const input = document.querySelector("#chat .aegis #input");
              const dt = new DataTransfer();
              dt.items.add(new File(["kept"], "kept.txt", {type: "text/plain"}));
              const ev = new ClipboardEvent("paste", {
                clipboardData: dt, bubbles: true, cancelable: true,
              });
              input.dispatchEvent(ev);
              return ev.defaultPrevented;
            }"""
        )
        is True
    )
    page.wait_for_selector("#chat .aegis #atts .chip", timeout=10_000)
    assert errors == []
    page.close()
