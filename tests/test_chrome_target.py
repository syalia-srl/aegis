"""The extension's target.js against Playwright's own selector engine: on the
same page, each target names the same elements in both, so a target that works
in a server browser works in the person's tab."""

from pathlib import Path

import pytest

from .conftest import browser_slot

pytestmark = [pytest.mark.browser, pytest.mark.slow]

playwright = pytest.importorskip("playwright.sync_api")

TARGET = (
    Path(__file__).parent.parent / "src" / "aegis" / "extension" / "target.js"
).read_text()

PAGE = """<!doctype html><title>Fixture</title>
<h1>Sign up</h1><h2>Details</h2>
<nav><a href="/">Home</a> <a href="/help">Help</a> <a>No href</a></nav>
<form>
  <label for="email">Email</label> <input id="email" placeholder="you@x.org">
  <label>Password <input type="password"></label>
  <input aria-label="Search the site" type="search">
  <span id="cap">Comment</span><textarea aria-labelledby="cap"></textarea>
  <select aria-label="Country"><option>Cuba</option><option>Spain</option></select>
  <input type="checkbox" id="tos"><label for="tos">I agree</label>
  <button type="submit">Send</button>
  <button aria-label="Close dialog">x</button>
  <input type="submit" value="Save draft">
  <div role="button">Custom</div>
  <button style="display:none">Hidden send</button>
</form>
<p>Sign in to <b>continue</b></p>
<ul><li>One</li><li>Two</li></ul>
<img src="data:," alt="Logo">
"""

TARGETS = [
    "role=heading",
    'role=heading[name="Sign up"]',
    "role=heading[level=2]",
    "role=link",
    'role=link[name="Help"]',
    'role=link[name="help"]',
    'role=link[name="help" i]',
    "role=button",
    'role=button[name="Send"]',
    'role=button[name="close"]',
    'role=button[name="Close dialog"]',
    'role=button[name="Save draft"]',
    "role=textbox",
    'role=textbox[name="Email"]',
    'role=textbox[name="Comment"]',
    "role=searchbox",
    "role=combobox",
    "role=checkbox",
    "role=listitem",
    'role=img[name="Logo"]',
    "text=Sign in",
    "text=continue",
    'text="Send"',
    "text=one",
    "#email",
    "form button",
    "css=li",
]

LABELS = ["Email", "Password", "Search the site", "Comment", "Country", "I agree"]


@pytest.fixture(scope="module")
def page():
    with browser_slot(), playwright.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(PAGE)
        yield pg
        b.close()


def ours(page, target: str) -> list[str]:
    return page.evaluate(
        f"(sel) => {{ {TARGET}; return aegisTarget().find(sel).map(e => e.outerHTML); }}",
        target,
    )


@pytest.mark.parametrize("target", TARGETS)
def test_a_target_names_the_same_elements_as_playwright(page, target):
    theirs = page.locator(target).evaluate_all("els => els.map(e => e.outerHTML)")
    assert ours(page, target) == theirs


@pytest.mark.parametrize("label", LABELS)
def test_a_label_names_the_same_field_as_get_by_label(page, label):
    theirs = page.get_by_label(label).evaluate_all("els => els.map(e => e.outerHTML)")
    assert ours(page, f"label={label}") == theirs


def test_a_bad_target_says_why(page):
    with pytest.raises(Exception, match="cannot read"):
        ours(page, "role=button[[")


def test_the_outline_reads_the_page_by_roles(page):
    text = page.evaluate(f"() => {{ {TARGET}; return aegisTarget().outline(); }}")
    assert '- heading "Sign up" [level=1]' in text
    assert '- textbox "Email"' in text and '- button "Send"' in text
    assert "Hidden send" not in text
