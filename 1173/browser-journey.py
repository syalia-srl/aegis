"""One-off acceptance capture against a fresh aegis serve."""

import os
from pathlib import Path

import pytest

from .test_browser import CONFIG, Server
from .test_browser_knowledge import browser, edit, open_view, page, seed_view

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def server(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    running = Server(tmp_path, fake_claude, fake_opencode)
    running.args = ["--name", "knowledge-demo"]
    running.start()
    try:
        yield running
    finally:
        running.stop()


def test_knowledge_desktop_and_phone(server, page):
    output = Path(os.environ["KNOWLEDGE_EVIDENCE_DIR"])
    seed_view(server.root)
    proposed = ".krow[data-name=merge-after-green]"
    expired = ".krow[data-name=release-freeze]"

    page.set_viewport_size({"width": 1366, "height": 900})
    open_view(page, server)
    assert page.locator(proposed + " .fl").inner_text() == "proposed"
    assert page.locator(expired + " .fl").inner_text() == "expired"
    print("Desktop 1366 x 900: legacy proposal and expired memory are visible.")
    page.click(proposed)
    edit(
        page,
        "---\nname: merge-after-green\ndescription: Merge after every check passes\n"
        "status: proposed\n---\nVerify the release checks before merging.\n",
    )
    page.click(".ksave")
    page.wait_for_function("document.querySelector('.knote').textContent.includes('Saved')")
    page.wait_for_selector(".khist .kver .kvnow")
    assert page.locator(".khist .kver .kvm").inner_text() == "edit memory merge-after-green"
    assert page.locator(proposed + " .fl").inner_text() == "ok"
    page.click(expired)
    page.wait_for_function("location.hash === '#knowledge=release-freeze'")
    page.click(proposed)
    page.wait_for_selector(".khist .kver .kvnow")
    assert "status: approved" in page.locator(".kdetail .ked .cm-content").inner_text()
    page.screenshot(path=output / "knowledge-desktop.png")
    print("Desktop: opened the proposal, saved it approved, and saw its Git edit in History.")

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(server.url + "#knowledge")
    page.wait_for_selector(expired)
    assert page.locator(expired + " .fl").inner_text() == "expired"
    page.click(expired)
    page.wait_for_selector(".kdetail .ked .cm-content")
    assert page.locator(".kdetail .kname").inner_text() == "release-freeze"
    assert page.locator(".kdetail .khead .fl").inner_text() == "expired"
    assert not page.locator(".klist").is_visible()
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width == 390
    page.screenshot(path=output / "knowledge-phone-390.png")
    page.click(".kback")
    page.wait_for_selector(expired)
    assert page.locator(".klist").is_visible()
    assert page.errors == []
    print(f"Phone {width} x 844: opened the expired memory full width, then returned to the list.")
    print("Chromium reported zero console errors.")
