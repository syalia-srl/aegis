# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The client's cascade after the ``.aegis`` rewrite (plan 2026-11-08, a3).

Lowering ``#a2`` to a class lowers specificity, and a lowered rule can lose to
one it used to beat. ``scripts/cascade_baseline.py`` recorded both halves of
the check at the slice's base commit: the specificity ledger of every mount
selector, and the computed styles of a fixed inventory at 1280 and 390 px.
These tests hold the rewrite to that file:

* static: every selector of a mount's stylesheet is scoped under ``.aegis``,
  nothing mentions the old id, each selector moved exactly one id down and one
  class up, and no selector that stayed unscoped now outranks one that moved;
* browser: the same inventory, re-measured, moves nothing.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from .test_browser import browser, server  # noqa: F401

pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).parents[1]
CLIENT = ROOT / "src" / "aegis" / "client"
BASELINE = ROOT / "tests" / "fixtures" / "cascade-baseline.json"

SCRIPT = ROOT / "scripts" / "cascade_baseline.py"
_spec = importlib.util.spec_from_file_location("cascade_baseline", SCRIPT)
cascade = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cascade)

# What only the standalone page loads, and what may stay unscoped there: rules
# for elements outside the mount's root. #probes is the page's, not the mount's.
PAGE_RULES = {"*", "html", "body", "#probes"}


def sheet_paths() -> list[Path]:
    return [CLIENT / "css" / "client.css", *sorted((CLIENT / "themes").glob("*.css"))]


def ledger() -> dict[str, dict[str, tuple[int, int, int]]]:
    """sheet -> selector -> specificity, for the tree as it is now."""
    got = {}
    for path in sheet_paths():
        text = path.read_text()
        got[cascade.sheet_name(path)] = {
            s: cascade.specificity(s) for s in cascade.selectors(text)
        }
    return got


def expected_after(sel: str) -> str:
    """The selector the rewrite should have produced from a baseline one."""
    s = re.sub(r"\[data-theme=([a-z]+)\]\s*#a2", r".aegis[data-theme=\1]", sel)
    s = s.replace("#a2", ".aegis")
    if s.startswith(".tabcard"):
        s = ".aegis " + s
    if s == ":where(svg.ic)":
        s = ".aegis :where(svg.ic)"
    return s


def expected_specificity(
    sel: str, before: tuple[int, int, int]
) -> tuple[int, int, int]:
    """What the baseline selector's specificity becomes after the rewrite."""
    a, b, c = before
    if "#a2" in sel:
        return (a - 1, b + 1, c)  # one id down, one class up
    if sel.startswith(".tabcard"):
        return (a, b + 1, c)
    if sel == ":where(svg.ic)":
        return (0, 1, 0)  # the :where keeps the svg part at zero
    return before


@pytest.fixture(scope="module")
def before() -> dict[str, dict[str, tuple[int, int, int]]]:
    doc = json.loads(BASELINE.read_text())
    return {
        sheet: {sel: tuple(spec) for sel, spec in rows.items()}
        for sheet, rows in doc["ledger"].items()
    }


def test_no_mount_stylesheet_uses_the_standalone_id():
    """A mount's stylesheet must not name index.html's root id: in a host page
    there is no such element, and the rule would style nothing."""
    found = [
        f"{p.name}: {n}"
        for p in sheet_paths()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if "#a2" in line
    ]
    assert found == []


def test_every_mount_selector_is_scoped_under_the_root():
    """Nothing the mount loads may style an element the host owns."""
    unscoped = [
        f"{path.name}: {sel}"
        for path in sheet_paths()
        for sel in cascade.selectors(path.read_text())
        if ".aegis" not in sel
    ]
    assert unscoped == []


def test_page_css_holds_only_the_pages_own_rules():
    """The page's rules are for the standalone document, never for a mount, so
    none of them may be scoped to the root and only page-level selectors may
    appear."""
    text = (CLIENT / "css" / "page.css").read_text()
    sels = set(cascade.selectors(text))
    assert sels <= PAGE_RULES, sorted(sels - PAGE_RULES)
    assert ".aegis" not in text


def test_the_rewrite_lowered_every_selector_by_one_id(before):
    """One id out and one class in keeps the relative order of every selector
    the rewrite touched; a selector that moved differently is the bug the
    ledger exists to catch."""
    now = ledger()
    wrong = []
    for sheet, rows in before.items():
        assert sheet in now, f"{sheet} is gone"
        for sel, spec in rows.items():
            if sel in PAGE_RULES:
                continue  # page.css carries them now
            after = expected_after(sel)
            if after not in now[sheet]:
                wrong.append(f"{sheet} {sel}: expected {after} in the sheet")
                continue
            want = expected_specificity(sel, spec)
            if now[sheet][after] != want:
                wrong.append(
                    f"{sheet} {sel}: {now[sheet][after]} where {want} was expected"
                )
    assert wrong == []


def test_the_only_rules_rescoped_were_the_tab_card_and_the_icon_default(before):
    """Two families were not scoped by #a2: the tab card's own rules and the
    sprite-icon default. Scoping a component's own rules lets them win inside
    the component, where the old id used to beat them; inside the tab card
    that revives .tabcard .card's border:0 and the 15 px icons. Every other
    selector the rewrite touched was already scoped under #a2, so its relative
    order among the scoped rules is unchanged (the test above). This names the
    two exceptions so no future rule joins them unnoticed."""
    rows = before["css/client.css"]
    rescoped = sorted(s for s in rows if s not in PAGE_RULES and "#a2" not in s)
    tabcard = sorted(s for s in rescoped if s.startswith(".tabcard"))
    assert [s for s in rescoped if not s.startswith(".tabcard")] == [":where(svg.ic)"]
    assert tabcard and all(s.startswith(".tabcard") for s in tabcard)
    now = ledger()["css/client.css"]
    for sel in rescoped:
        after = expected_after(sel)
        assert after in now, after
        assert ".aegis" in after
    # The tab card is drawn inside the root it belongs to, so it matches.
    js = (CLIENT / "js" / "tabcard.js").read_text()
    assert "rootOf(list).append(box)" in js


def test_the_root_that_names_a_theme_starts_from_ink():
    """A root with a theme name and no theme file of its own (a host that loads
    client.css alone) still draws; the fallback is zero-specificity, and it is
    ink's own block, not a copy that can drift."""
    ink = (CLIENT / "themes" / "ink.css").read_text()
    client = (CLIENT / "css" / "client.css").read_text()
    fallback_sel = ":where(.aegis[data-theme]:not([data-theme=host]))"

    def block(text: str, prelude: str) -> list[str]:
        start = text.index(prelude) + len(prelude) + 1
        end = text.index("}", start)
        return [p.strip() for p in text[start:end].split(";") if p.strip()]

    got = block(client, fallback_sel)
    assert got == block(ink, ".aegis[data-theme=ink]")
    assert cascade.specificity(fallback_sel) == (0, 0, 0)
    assert ".aegis[data-layout=full]" in client
    assert ".aegis[data-layout=chat]" in client


def test_the_standalone_page_loads_both_stylesheets_and_is_a_mount():
    """index.html is the full layout of the same root a host mounts: the page
    rules, the client, and a root carrying the class, its theme and its
    layout."""
    html = (CLIENT / "index.html").read_text()
    assert 'href="__AEGIS_PREFIX__/static/css/page.css"' in html
    assert 'href="__AEGIS_PREFIX__/static/css/client.css"' in html
    root = re.search(r'<div id="a2" class="([^"]*)"([^>]*)>', html)
    assert root, "the page's root element"
    assert "aegis" in root.group(1).split()
    attrs = root.group(2)
    assert 'data-theme="ink"' in attrs
    assert 'data-layout="full"' in attrs


@pytest.mark.browser
@pytest.mark.slow
def test_the_computed_cascade_is_the_one_the_baseline_recorded(server, browser):
    """The other half: the client with a real server and a spawned session must
    compute the same inventory at 1280 and 390 px as the base commit did."""
    baseline = json.loads(BASELINE.read_text())
    errors: list[str] = []
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(server.url)
        session = cascade.spawn_session(page)
        measured = cascade.measure(page, session)
    finally:
        page.close()
    moved = cascade.diff(baseline["inventory"], measured)
    assert moved == [], "\n".join(moved)
    assert errors == []
