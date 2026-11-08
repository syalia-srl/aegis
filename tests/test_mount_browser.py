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
        yield info
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
    """The root is built and the socket's welcome has arrived."""
    page.wait_for_function(
        "() => document.querySelector('#chat > .aegis')"
        " && window.__wsMsgs.some((m) => m.t === 'welcome')",
        timeout=30_000,
    )


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
