"""The label tables in a real browser (plan 2026-11-08, slice b9).

The Python side (tests/test_labels.py) holds both tables to the same keys and
variables. This file proves the other half in the real client: the standalone
page mounted with ``?lang=es`` reads Spanish in the chrome every view shares,
and a chat mounted with ``lang: "es"`` in a stranger's page reads Spanish
inside the host's element while the host's own words stay its own.
"""

import pytest

from .test_browser import server  # noqa: F401
from .test_mount_browser import browser, host, open_page  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def spanish(url: str) -> str:
    return url + ("&" if "?" in url else "?") + "lang=es"


def test_the_full_layout_reads_spanish(server, browser):  # noqa: F811
    errors: list[str] = []
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append(str(e)))
    # The printed URL signs the browser in and redirects to "/", which drops
    # any query; the language then rides the second request, with the cookie.
    page.goto(server.url)
    page.wait_for_selector("#conn-dot.ready", state="attached")
    page.goto(spanish(server.url.split("?")[0]))
    page.wait_for_selector("#conn-dot.ready", state="attached")
    assert page.text_content("#tab-fleet") == "Flota"
    assert page.text_content("#tab-add .lbl") == "Nueva sesión"
    assert page.text_content("#settings-btn .lbl") == "Ajustes"
    page.click("#settings-btn")
    page.wait_for_selector("#a2[data-view=settings]")
    assert page.text_content("#settings").strip()  # the view drew; nothing threw
    assert not errors, errors
    page.close()


def test_a_chat_mounts_in_spanish(host, browser):  # noqa: F811
    page, errors = open_page(browser, 390)
    page.goto(spanish(host["url"]))
    page.wait_for_selector("#chat > .aegis")
    assert page.get_attribute("#chat textarea#input", "placeholder").startswith(
        "Escríbele al agente."
    )
    assert page.get_attribute("#chat #send", "title") == "Enviar (Enter)"
    # A card the transcript draws (the schedule approval card's body), built
    # through the module with the Spanish table: a host's Spanish chat must
    # not fall back to English words inside its cards.
    card = page.evaluate(
        """async () => {
          const { scheduleWorkflow } = await import(`${window.aegisHost.prefix}/static/js/entries.js`);
          const { labelsFor } = await import(`${window.aegisHost.prefix}/static/js/labels.js`);
          const box = document.createElement("div");
          box.append(...scheduleWorkflow({
            id: "nightly", title: "Nightly", sha: "1234567890abcdef", changed: false,
            say: "Do it", steps: ["Step one"],
            saved_by: { by: { handle: "ana" }, t: 0 },
          }, labelsFor("es")));
          return box.textContent;
        }"""
    )
    assert "ejecuciones" in card and "guardado por ana" in card, card
    assert "Flota" not in page.text_content("#board")  # the host's page is untouched
    assert not errors, errors
    page.close()
