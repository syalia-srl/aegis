"""Settings → Phone app (#1060) in a real browser: inside the Android app the
page offers to pair this phone and asks the app to; a phone paired over HTTP
is listed and can be unpaired."""

# ruff: noqa: F811  (pytest fixtures imported from test_browser)

import httpx
import pytest

from .fake_device import Phone
from .test_browser import browser, open_settings, page, settings_server  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

# What the Android app injects: window.aegisShell answering JSON by request id.
FAKE_SHELL = """
window.__asked = [];
window.aegisShell = {
  onmessage: null,
  postMessage(text) {
    const msg = JSON.parse(text);
    window.__asked.push(msg.method);
    setTimeout(() => this.onmessage({ data: JSON.stringify({ id: msg.id, result: { paired: true } }) }), 0);
  },
};
"""


def pair_over_http(url: str) -> Phone:
    phone = Phone()
    with httpx.Client(base_url=url.split("?")[0]) as c:
        c.get(url)  # ?token= becomes the cookie
        r = c.post(
            "/api/devices/pair", json={"name": "Pixel", "public_key": phone.public_key}
        )
        r.raise_for_status()
    return phone


@pytest.mark.parametrize("width", [1280, 390])
def test_inside_the_app_the_section_pairs_and_lists_the_phone(
    settings_server, page, width
):
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(FAKE_SHELL)
    pair_over_http(settings_server.url)
    open_settings(page, settings_server.url)
    page.wait_for_selector("#set-phone .set-phone-device")
    assert "Pixel" in page.inner_text("#set-phone")
    page.click("#set-phone-pair")
    page.wait_for_function("window.__asked.includes('pair')")
    page.click("#set-phone .set-phone-device button")
    page.wait_for_selector("#set-phone-none")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.errors == []


def test_outside_the_app_on_a_desktop_with_no_phone_the_section_is_hidden(
    settings_server, page
):
    open_settings(page, settings_server.url)
    assert page.locator("#set-phone").count() == 0
