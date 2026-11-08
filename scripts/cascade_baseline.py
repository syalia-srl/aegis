"""The client's cascade, measured before and after the ``.aegis`` rewrite.

Slice a3 (plan 2026-11-08, "the mountable parts") lowers every ``#a2``
selector in the client's stylesheets to the root class ``.aegis``. That lowers
specificity, and a lowered rule can lose to one it used to beat. This script
records both halves of the check, so the audit test can compare rather than
argue:

* the **specificity ledger**: every selector in ``css/client.css`` and the
  themes, with its specificity;
* the **computed baseline**: about thirty stable properties for a fixed
  inventory of elements, at 1280 and 390 px, on Fleet, a session, New session
  and Settings.

It runs one ``aegis serve`` (the fake claude the browser tests use) and one
browser pass. ``tests/test_css_scope.py`` re-measures the client and diffs it
against ``tests/fixtures/cascade-baseline.json`` name by name.

    uv run python scripts/cascade_baseline.py            # write the baseline
    uv run python scripts/cascade_baseline.py --check    # compare and report

Run it at the slice's base commit, before any stylesheet edit, so the file it
writes describes the client the rewrite must preserve.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
CLIENT = ROOT / "src" / "aegis" / "client"
OUT = ROOT / "tests" / "fixtures" / "cascade-baseline.json"

# Where the old name and the new one each live. The baseline is written with
# base.css; the audit reads client.css.
CLIENT_SHEET = "css/client.css"
OLD_CLIENT_SHEET = "css/base.css"
PAGE_SHEET = "css/page.css"


# The file name a sheet is recorded under, whichever tree is being read.
def sheet_name(path: Path) -> str:
    return path.relative_to(CLIENT).as_posix().replace("css/base.css", CLIENT_SHEET)


# -- the stylesheets ---------------------------------------------------------


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def selectors(text: str) -> list[str]:
    """Every selector in a stylesheet, at any depth, in file order.

    A rule's prelude is the text before its ``{``; a prelude that starts with
    ``@`` opens an at-rule and is not a selector. Everything under
    ``@keyframes`` is skipped: its blocks are offsets, not selectors.
    """
    text = strip_comments(text)
    found: list[str] = []
    stack: list[str] = []
    buf = ""
    for ch in text:
        if ch == "{":
            prelude = buf.strip()
            buf = ""
            if prelude.startswith("@"):
                stack.append(prelude)
            else:
                if not any(a.startswith("@keyframes") for a in stack):
                    found.extend(split_selector(prelude))
                stack.append("rule")
        elif ch == "}":
            if stack:
                stack.pop()
            buf = ""
        else:
            buf += ch
    return found


def split_selector(prelude: str) -> list[str]:
    """A prelude split on its top-level commas."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(prelude):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(prelude[start:i].strip())
            start = i + 1
    parts.append(prelude[start:].strip())
    return [p for p in parts if p]


def _consume_parens(sel: str, i: int) -> int:
    """The index after the ``)`` matching the ``(`` at ``i``."""
    depth = 0
    while i < len(sel):
        if sel[i] == "(":
            depth += 1
        elif sel[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def specificity(sel: str) -> tuple[int, int, int]:
    """CSS specificity as (ids, classes, elements).

    ``:where()`` is zero; ``:is()``, ``:not()`` and ``:has()`` take their most
    specific argument. The client uses nothing else that rewrites specificity.
    """
    a = b = c = 0
    i = 0
    while i < len(sel):
        ch = sel[i]
        if ch == "#":
            m = re.match(r"#([\w-]+)", sel[i:])
            if m:
                a += 1
                i += m.end()
                continue
        elif ch == ".":
            m = re.match(r"\.([\w-]+)", sel[i:])
            if m:
                b += 1
                i += m.end()
                continue
        elif ch == "[":
            j = sel.find("]", i)
            b += 1
            i = len(sel) if j < 0 else j + 1
            continue
        elif ch == ":":
            m = re.match(r":([\w-]+)", sel[i:])
            if not m:
                i += 1
                continue
            name = m.group(1)
            i += m.end()
            if name == "where":
                i = _consume_parens(sel, i - 1)
                continue
            if name in ("is", "not", "has", "matches", "nth-child", "nth-last-child"):
                end = _consume_parens(sel, i - 1)
                inner = sel[i : end - 1]
                if name.startswith("nth-"):
                    b += 1
                else:
                    a1, b1, c1 = max(
                        (specificity(s) for s in split_selector(inner)),
                        default=(0, 0, 0),
                    )
                    a, b, c = a + a1, b + b1, c + c1
                i = end
                continue
            if name == "before" and sel[i - 2] == ":":
                c += 1
                continue
            b += 1
            continue
        elif ch == "*":
            i += 1
            continue
        else:
            m = re.match(r"[a-zA-Z][\w-]*", sel[i:])
            if m:
                c += 1
                i += m.end()
                continue
        i += 1
    return a, b, c


def read_sheets() -> dict[str, dict[str, list[int]]]:
    """The ledger of one tree: sheet -> selector -> specificity.

    The client sheet is ``client.css`` or, at the base commit, ``base.css``;
    either is recorded as ``css/client.css`` so the two trees compare.
    """
    paths = [CLIENT / "css" / "client.css", *sorted((CLIENT / "themes").glob("*.css"))]
    if not paths[0].exists():
        paths[0] = CLIENT / OLD_CLIENT_SHEET
    ledger: dict[str, dict[str, list[int]]] = {}
    for path in paths:
        rules: dict[str, list[int]] = {}
        for sel in selectors(path.read_text()):
            rules[sel] = list(specificity(sel))
        ledger[sheet_name(path)] = rules
    return ledger


# -- the computed baseline ---------------------------------------------------

# The properties compared. Colours, type, boxes and layout: values a cascade
# change moves, and not ones the session's generated id or a clock can.
PROPERTIES = [
    "color",
    "background-color",
    "border-top-color",
    "border-left-color",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "line-height",
    "letter-spacing",
    "text-transform",
    "text-align",
    "border-top-width",
    "border-top-style",
    "border-top-left-radius",
    "display",
    "position",
    "flex-direction",
    "flex-wrap",
    "grid-template-columns",
    "gap",
    "margin-top",
    "margin-left",
    "padding-top",
    "padding-left",
    "overflow-x",
    "overflow-y",
    "z-index",
    "opacity",
    "box-shadow",
    "min-height",
]

# Row tracks whose size is the content's: only a structural element's rows say
# something about the cascade, and only these never grow with a session's text.
EXTRA = {
    "#a2": ["grid-template-rows"],
    "#a2 .v-session": ["grid-template-rows"],
    "#a2 .v-spawn": ["grid-template-rows"],
    "#a2 .v-fleet": ["grid-template-rows"],
}

# The fixed inventory. A selector that is not on screen is recorded as null, so
# an element that appears or disappears is a diff too.
INVENTORY: dict[str, list[str]] = {
    "common": [
        "#a2",
        "#a2 .tabs",
        "#a2 .tab.home",
        "#a2 .tablist",
        "#a2 .new-session",
        "#a2 .hdr-menu",
        "#a2 .servers",
        "#a2 .view",
        "#a2 button",
        "#a2 .btn",
        "#a2 svg.ic",
    ],
    "fleet": [
        "#a2 .v-fleet",
        "#a2 .v-fleet .band",
        "#a2 .v-fleet .band h5",
        "#a2 .v-fleet .order",
        "#a2 .v-fleet .seg button",
        "#a2 .v-fleet .cards",
        "#a2 .v-fleet .cards .card",
        "#a2 .v-fleet .cards .card .ln",
    ],
    "session": [
        "#a2 .v-session",
        "#a2 .v-session .col.pane",
        "#a2 .v-session .tr",
        "#a2 .v-session .row",
        "#a2 .v-session .row .t",
        "#a2 .v-session .row .body",
        "#a2 .v-session .row.user .body",
        "#a2 .v-session .row.prose .body",
        "#a2 .v-session .composer",
        "#a2 .v-session .composer .box",
        "#a2 .v-session .composer textarea",
        "#a2 .v-session .composer .send",
        "#a2 .v-session .side",
        "#a2 .v-session .side .title",
        "#a2 .v-session .side .sec",
        "#a2 .v-session .side h4",
    ],
    "spawn": [
        "#a2 .v-spawn",
        "#a2 .v-spawn .spawn",
        "#a2 .v-spawn .spawn .box",
        "#a2 .v-spawn .spawn textarea",
        "#a2 .v-spawn .spawn .pick",
        "#a2 .v-spawn .spawn .send",
        "#a2 .v-spawn .spawn .chips",
    ],
    "settings": [
        "#a2 .v-settings",
        "#a2 .v-settings .settings",
        "#a2 .v-settings .settings h3",
        "#set-harnesses .set-harness",
        "#a2 .v-settings .settings .chip",
        "#a2 .v-settings .settings .pick",
    ],
}

VIEWS = ("fleet", "session", "spawn", "settings")
WIDTHS = (1280, 390)

READY = {
    "fleet": "#a2[data-view=fleet] .cards .card",
    "session": "#a2[data-view=session] .tr",
    "spawn": "#a2[data-view=spawn] .spawn",
    "settings": "#set-harnesses .set-harness",
}

MEASURE_JS = """
({ selectors, props, extra }) => {
  const out = {};
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (!el) { out[sel] = null; continue; }
    const cs = getComputedStyle(el);
    const row = {};
    for (const p of props.concat(extra[sel] || [])) row[p] = cs.getPropertyValue(p);
    out[sel] = row;
  }
  return out;
}
"""


def goto_view(page, view: str, session: str | None) -> None:
    """Put the page on one view by its hash, as Back and a hand-edited URL do."""
    if view == "fleet":
        page.evaluate("location.hash = '#fleet'")
    elif view == "session":
        page.evaluate("id => { location.hash = '#s=' + id; }", session)
    elif view == "spawn":
        page.evaluate("location.hash = '#new'")
    elif view == "settings":
        page.evaluate("location.hash = '#settings'")
    else:  # pragma: no cover - a typo in VIEWS
        raise ValueError(view)
    page.wait_for_selector(READY[view], timeout=15000)


def spawn_session(page) -> str:
    """One session with one answered turn, through the UI, and its id."""
    page.wait_for_selector("#conn-dot.ready", state="attached", timeout=15000)
    page.click("#tab-add")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.wait_for_function("document.querySelector('#sp-agent').value !== ''")
    page.fill("#sp-text", "hello")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_selector("#a2 #entries .row.prose", timeout=15000)
    return page.evaluate("location.hash.slice(3)")


def measure(page, session: str, widths=WIDTHS) -> dict:
    """The inventory's computed styles, per width and view."""
    everything: dict[str, dict[str, dict]] = {}
    for width in widths:
        page.set_viewport_size({"width": width, "height": 800})
        per_view: dict[str, dict] = {}
        for view in VIEWS:
            goto_view(page, view, session)
            page.wait_for_function("() => document.fonts.status === 'loaded'")
            sels = INVENTORY["common"] + INVENTORY[view]
            per_view[view] = page.evaluate(
                MEASURE_JS,
                {"selectors": sels, "props": PROPERTIES, "extra": EXTRA},
            )
        everything[str(width)] = per_view
    return everything


def diff(baseline: dict, measured: dict) -> list[str]:
    """Every place a computed value moved, as ``width view selector prop: a -> b``."""
    moved: list[str] = []
    for width, views in baseline.items():
        got_views = measured.get(width, {})
        for view, rows in views.items():
            got_rows = got_views.get(view, {})
            for sel, props in rows.items():
                if sel not in got_rows:
                    moved.append(f"{width} {view} {sel}: not measured (element gone)")
                    continue
                now = got_rows[sel]
                if props is None or now is None:
                    if props is not now:
                        moved.append(
                            f"{width} {view} {sel}: element appeared or vanished"
                        )
                    continue
                for prop, before in props.items():
                    after = now.get(prop)
                    if before != after:
                        moved.append(
                            f"{width} {view} {sel} {prop}: {before} -> {after}"
                        )
    return moved


# -- the harness -------------------------------------------------------------


def isolated_env(tmp: Path) -> dict[str, str]:
    """No real credentials, quota cache or network, as the browser tests run."""
    return {
        "CLAUDE_CREDS": str(tmp / "claude-credentials.json"),
        "CLAUDE_CONFIG": str(tmp / "claude.json"),
        "OPENCODE_AUTH": str(tmp / "opencode-auth.json"),
        "AEGIS_QUOTA_CACHE": str(tmp / "cache"),
        "AEGIS_CLAUDE_TOKEN_FILE": str(tmp / "claude-oauth-token"),
        "AEGIS_DICTATION_DIR": str(tmp / "dictation"),
        "AEGIS_NET_PROBES": "offline",
    }


def fake_claude(tmp: Path) -> str:
    """An executable that runs the browser tests' fake claude."""
    path = tmp / "bin" / "claude"
    path.parent.mkdir(parents=True)
    home = tmp / "fake-home"
    home.mkdir()
    fake = ROOT / "tests" / "fake_claude.py"
    path.write_text(
        f'#!/bin/sh\nexport FAKE_CLAUDE_HOME="{home}"\n'
        f'exec "{sys.executable}" "{fake}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def git_head() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare, write nothing")
    parser.add_argument("--out", type=Path, default=OUT)
    opts = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from playwright.sync_api import sync_playwright

    from tests.conftest import browser_slot
    from tests.test_browser import CONFIG, Server

    tmp = Path(tempfile.mkdtemp(prefix="aegis-cascade-"))
    os.environ.update(isolated_env(tmp))
    (tmp / ".aegis.yaml").write_text(CONFIG)
    server = Server(tmp, fake_claude(tmp)).start()
    try:
        with browser_slot(), sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.goto(server.url)
                session = spawn_session(page)
                measured = measure(page, session)
            finally:
                browser.close()
    finally:
        server.stop()

    if opts.check:
        baseline = json.loads(opts.out.read_text())
        moved = diff(baseline["inventory"], measured)
        for line in moved:
            print(line)
        print(f"{len(moved)} computed value(s) moved")
        return 1 if moved else 0

    doc = {
        "base": git_head(),
        "widths": list(WIDTHS),
        "views": list(VIEWS),
        "properties": PROPERTIES,
        "extra": EXTRA,
        "inventory": measured,
        "ledger": read_sheets(),
    }
    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(
        f"wrote {opts.out.relative_to(ROOT)} ({len(read_sheets()[CLIENT_SHEET])} selectors)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
