"""Groups in the client (#1039): groups.js's blocks, and the bar that draws them."""

import re

import pytest

from .test_agents import mcp
from .test_browser import (  # noqa: F401
    browser,
    playwright,
    report,
    server,
    spawn,
    turns_done,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def m(key, created, group=None, at=None, handle=None, **kw):
    lid = key.split("/")[-1]
    return {
        "key": key,
        "log_id": lid,
        "created_at": created,
        "group": group,
        "server": at,
        "handle": handle or lid,
        **kw,
    }


def blocks(pg, metas) -> list:
    """[{key, head, members}] from groups.js's blocksOf, the head flattened."""
    return pg.evaluate(
        """async ([metas]) => {
          const g = await import('/static/js/groups.js');
          return g.blocksOf(metas, 'alpha').map((b) => ({
            key: b.key,
            head: {kind: b.head.kind || 'session', key: b.head.key,
                   name: b.head.name ?? null, mark: b.head.mark ?? null},
            members: b.members.map((x) => x.key),
          }));
        }""",
        [metas],
    )


def popped(pg, metas, key, folded=True, focus=None) -> list:
    """The member keys poppedOut() draws beside the block of `key`."""
    return pg.evaluate(
        """async ([metas, key, folded, focus]) => {
          const g = await import('/static/js/groups.js');
          const b = g.blocksOf(metas, 'alpha').find((x) => x.key === key);
          return g.poppedOut(b, folded, focus).map((m) => m.key);
        }""",
        [metas, key, folded, focus],
    )


def test_blocks_of_named_groups(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    g = "grp-3fa9c210"
    a = m("a", 1, g, group_name="brisk-backus", mark="working")
    b = m("b", 2, g, group_name="brisk-backus", mark="needs_you")
    one = m("c", 3, "grp-77777777", group_name="lonely-ones")
    lone = m("d", 4)
    term = m("t", 5, g, kind="term")
    web = m("x", 6, g, kind="browser")
    assert blocks(page, [a, b, one, lone, term, web]) == [
        {
            "key": f"{g}@alpha",
            "head": {
                "kind": "group",
                "key": f"{g}@alpha",
                "name": "brisk-backus",
                "mark": "needs_you",
            },
            "members": ["a", "b"],
        },
        {
            "key": "grp-77777777@alpha",
            "head": {"kind": "session", "key": "c", "name": None, "mark": None},
            "members": [],
        },
        {
            "key": "d@alpha",
            "head": {"kind": "session", "key": "d", "name": None, "mark": None},
            "members": [],
        },
        {
            "key": "t@alpha",
            "head": {"kind": "term", "key": "t", "name": None, "mark": None},
            "members": [],
        },
        {
            "key": "x@alpha",
            "head": {"kind": "browser", "key": "x", "name": None, "mark": None},
            "members": [],
        },
    ]
    # An offline member leaves the group a lone open tab, the group keeping
    # its place and its record.
    assert blocks(page, [dict(a, off=True), b, lone])[0] == {
        "key": f"{g}@alpha",
        "head": {"kind": "session", "key": "b", "name": None, "mark": "needs_you"},
        "members": ["a"],
    }
    assert page.errors == []


def test_a_far_log_id_key_is_named_by_its_session(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    boss = m("20261101-0f0a", 1, handle="tab-groups")
    kid = m("beta/e", 2, "20261101-0f0a", at="beta")
    kid2 = m("beta/f", 3, "20261101-0f0a", at="beta")
    got = blocks(page, [boss, kid, kid2])
    far = next(b for b in got if b["key"] == "20261101-0f0a@beta")
    assert far["head"]["kind"] == "group"
    assert far["head"]["name"] == "tab-groups"
    # Its server archived the session the key names: the key is the name.
    far = next(b for b in blocks(page, [kid, kid2]) if b["key"] == "20261101-0f0a@beta")
    assert far["head"]["name"] == "20261101-0f0a@beta"
    assert page.errors == []


def test_popped_out_members_per_mark(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    g = "grp-3fa9c210"

    def meta(key, mark):
        return m(key, 1, g, group_name="brisk-backus", mark=mark)

    for mark in ("needs_you", "review", "done", "error"):
        metas = [meta("a", "working"), meta("b", mark)]
        assert popped(page, metas, f"{g}@alpha") == ["b"], mark
    for mark in ("working", "waiting"):
        metas = [meta("a", "working"), meta("b", mark)]
        assert popped(page, metas, f"{g}@alpha") == [], mark
    # An unfolded block pops nothing, and a lone tab never does.
    metas = [meta("a", "working"), meta("b", "needs_you")]
    assert popped(page, metas, f"{g}@alpha", folded=False) == []
    assert popped(page, [m("d", 4)], "d@alpha") == []
    # The member in view pops out whatever its mark.
    metas = [meta("a", "working"), meta("b", "needs_you"), meta("c", "working")]
    assert popped(page, metas, f"{g}@alpha", focus="c") == ["b", "c"]
    assert popped(page, metas, f"{g}@alpha", focus="b") == ["b"]
    assert page.errors == []


def slash_spawn(pg, key: str) -> str:
    """A person's /spawn typed in session `key`: the new session's key."""
    pg.goto(pg.url.split("#")[0] + f"#s={key}")
    pg.wait_for_selector(f".tab.on[data-id='{key}']")
    before = set(
        pg.eval_on_selector_all(
            "#tablist .tab[data-id]", "ts => ts.map(t => t.dataset.id)"
        )
    )
    pg.fill("#input", "/spawn opus")
    pg.press("#input", "Enter")
    pg.wait_for_function(
        f"document.querySelectorAll('#tablist .tab[data-id]').length === {len(before) + 1}"
    )
    after = pg.eval_on_selector_all(
        "#tablist .tab[data-id]", "ts => ts.map(t => t.dataset.id)"
    )
    return next(k for k in after if k not in before)


def bar(pg) -> list:
    """The bar as [[head, [member keys]], ...]: a group's head is its key, and
    a popped-out member is a block of its own, `["out", [key]]`."""
    return pg.eval_on_selector_all(
        "#tablist .tblock",
        """bs => bs.map((b) => {
             const ts = [...b.querySelectorAll('.tab')];
             if (b.classList.contains('out')) return ['out', ts.map((t) => t.dataset.id)];
             const head = ts.find((t) => t.classList.contains('named'));
             const ids = ts.filter((t) => t.dataset.id).map((t) => t.dataset.id);
             return [head ? head.dataset.group : ids[0] ?? null, head ? ids : []];
           })""",
    )


def group_key(pg) -> str:
    """The key of the group the bar draws as one block."""
    return pg.get_attribute("#tablist .tab.named", "data-group")


def group_name(pg) -> str:
    return pg.inner_text("#tablist .tab.named .hn")


def three_and_one(pg, srv):
    pg.goto(srv.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    parent = spawn(pg, "hello")
    slash_spawn(pg, parent)
    slash_spawn(pg, parent)
    d = spawn(pg)
    # Two spawns in the same second share created_at, so the bar's order of
    # them is the server's: take it rather than the order they were asked for.
    pg.wait_for_selector(".tblock.grp .tab[data-id]")
    a, b, c = pg.eval_on_selector_all(
        "#tablist .tblock.grp .tab[data-id]",
        "ts => ts.slice(0, 3).map((t) => t.dataset.id)",
    )
    assert a == parent
    return a, b, c, d


def test_a_spawn_draws_a_named_block_that_folds(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    child = slash_spawn(page, a)
    assert page.locator(".tblock.grp").count() == 1
    assert page.locator(".tab.named").count() == 1
    # The head is the group, not a session: it carries the group's name.
    assert page.get_attribute(".tab.named", "data-id") is None
    name = group_name(page)
    assert re.fullmatch(r"[a-z]+-[a-z]+(-\d+)?", name), name
    key = group_key(page)
    assert bar(page) == [[key, [a, child]]]
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    assert page.inner_text(".tab.named .cnt") == "+2"
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 3")
    assert page.errors == []


def test_a_session_spawned_from_another_joins_its_block(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    key = group_key(page)
    assert bar(page) == [[key, [a, b, c]], [d, []]]
    assert page.is_visible(".tblock.grp .tab.named")
    assert page.locator(".tblock.grp").count() == 1
    assert page.errors == []


def test_folding_hides_members_and_survives_a_reload(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    key = group_key(page)
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    assert page.inner_text(".tab.named .cnt") == "+3"
    page.reload()
    page.wait_for_selector(".tab.named")
    assert bar(page)[0] == [key, []]
    assert [d, []] in bar(page)
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 4")
    assert page.errors == []


BAR_JS = """want => JSON.stringify([...document.querySelectorAll('#tablist .tblock')]
  .map((b) => {
    const ts = [...b.querySelectorAll('.tab')];
    const named = ts.find((t) => t.classList.contains('named'));
    const ids = ts.filter((t) => t.dataset.id).map((t) => t.dataset.id);
    return [named ? named.dataset.group : (ids[0] ?? null), named ? ids : []];
  })) === JSON.stringify(want)"""


def drop(pg, src: str, dst: str, side: str) -> None:
    """Drag the tab at selector `src` onto the tab at selector `dst`, through
    the app's own dragstart, dragover and drop handlers. A mouse drag is what
    a person does, but Chromium's synthetic one is swallowed whenever a
    session patch rebuilds the strip mid-gesture."""
    pg.evaluate(
        """([src, dst, side]) => {
          const from = document.querySelector(src), to = document.querySelector(dst);
          const dt = new DataTransfer();
          const at = () => {
            const r = to.getBoundingClientRect();
            return {bubbles: true, dataTransfer: dt,
                    clientX: side === 'left' ? r.left + 3 : r.right - 3,
                    clientY: r.top + r.height / 2};
          };
          from.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
          to.dispatchEvent(new DragEvent('dragover', at()));
          to.dispatchEvent(new DragEvent('drop', at()));
          from.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
        }""",
        [src, dst, side],
    )


def dropped(pg, src: str, dst: str, side: str, want: list) -> None:
    """A drop whose outcome `want` the bar must show. A session patch can
    redraw the bar mid-drag and swallow the drop; a second try is harmless
    because these drops are idempotent."""
    for _ in range(2):
        drop(pg, src, dst, side)
        try:
            pg.wait_for_function(BAR_JS, arg=want, timeout=2500)
            return
        except playwright.TimeoutError:
            continue
    assert bar(pg) == want


def test_the_named_head_drags_its_group_and_a_member_stays_inside(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    # A session patch mid-drag redraws the whole bar and swallows the drop:
    # drag once every tab is idle.
    page.wait_for_function(
        "() => !document.querySelector(\"#tablist .tab use[href='#g-work']\")"
    )
    key = group_key(page)
    dropped(
        page,
        f"#tablist .tab[data-id='{d}']",
        f"#tablist .tab[data-id='{a}']",
        "left",
        [[d, []], [key, [a, b, c]]],
    )
    # The group's own tab carries the whole block, back before the lone tab.
    dropped(
        page,
        "#tablist .tab.named",
        f"#tablist .tab[data-id='{d}']",
        "left",
        [[key, [a, b, c]], [d, []]],
    )
    dropped(
        page,
        f"#tablist .tab[data-id='{c}']",
        f"#tablist .tab[data-id='{b}']",
        "left",
        [[key, [a, c, b]], [d, []]],
    )
    drop(
        page, f"#tablist .tab[data-id='{b}']", f"#tablist .tab[data-id='{d}']", "left"
    )  # out: refused
    drop(
        page, f"#tablist .tab[data-id='{b}']", f"#tablist .tab[data-id='{b}']", "left"
    )  # itself
    page.wait_for_timeout(400)
    assert bar(page) == [[key, [a, c, b]], [d, []]]
    assert page.errors == []


def test_alt_shift_brackets_move_the_tab_in_front_as_a_drag_does(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    key = group_key(page)

    def press(arrow: str, at: str, want: list) -> None:
        page.click(f"#tablist .tab[data-id='{at}']")
        page.wait_for_selector(f"#tablist .tab.on[data-id='{at}']")
        page.keyboard.press(f"Alt+Shift+{arrow}")
        page.wait_for_function(BAR_JS, arg=want)

    press(
        "BracketLeft", d, [[d, []], [key, [a, b, c]]]
    )  # a lone tab passes a whole group
    press("BracketLeft", d, [[d, []], [key, [a, b, c]]])  # the first stays
    press("BracketLeft", c, [[d, []], [key, [a, c, b]]])  # a member, within its group
    press("BracketLeft", c, [[d, []], [key, [c, a, b]]])
    press("BracketLeft", c, [[d, []], [key, [c, a, b]]])  # never past the group's tab
    press("BracketRight", c, [[d, []], [key, [a, c, b]]])
    press("BracketRight", c, [[d, []], [key, [a, b, c]]])
    press("BracketRight", c, [[d, []], [key, [a, b, c]]])  # nor out of its group
    assert page.input_value("#input") == ""  # the chord typed nothing
    page.reload()
    page.wait_for_selector("#tablist .tblock")
    assert bar(page) == [[d, []], [key, [a, b, c]]]
    assert page.errors == []


def test_the_member_in_view_stays_popped_out_and_the_group_folded(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    key = group_key(page)
    page.click(f"#tablist .tab[data-id='{c}']")
    page.evaluate("document.hasFocus = () => false")
    report(page, attention="needs_you", line="Which build?", replies=[])
    turns_done(page, 1)
    page.evaluate("delete document.hasFocus")
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    page.wait_for_selector(f".tab.out[data-id='{c}']")
    # Back to the member that needs you: it stays drawn beside the folded
    # block, the block itself stays folded.
    page.goto(page.url.split("#")[0] + f"#s={c}")
    page.wait_for_selector(f"#tablist .tab.out.on[data-id='{c}']")
    assert page.locator(".tblock.grp .tab").count() == 1
    assert bar(page)[0] == [key, []]
    assert ["out", [c]] in bar(page)
    assert page.errors == []


def test_a_folded_member_that_needs_you_pops_out_and_goes_back(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    b = slash_spawn(page, a)
    key = group_key(page)
    page.goto(page.url.split("#")[0] + f"#s={b}")
    page.wait_for_selector(f"#tablist .tab.on[data-id='{b}']")
    page.evaluate("document.hasFocus = () => false")
    report(page, attention="needs_you", line="Which build?", replies=[])
    turns_done(page, 1)
    page.evaluate("delete document.hasFocus")
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    page.wait_for_selector(f".tab.out[data-id='{b}']")
    [[head, hidden], *outs] = bar(page)
    assert head == key and hidden == []
    assert ["out", [b]] in outs
    # Its next turn starts and the person looks elsewhere: the popped-out tab
    # goes back inside (while in view it stays out, whatever its mark).
    page.fill("#input", '/script [["sleep", 3]]')
    page.press("#input", "Enter")
    page.goto(page.url.split("#")[0] + "#fleet")
    page.wait_for_function(f"!document.querySelector(\".tab.out[data-id='{b}']\")")
    assert ["out", [b]] not in bar(page)
    assert page.errors == []


def test_a_members_menu_archives_its_group(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    page.click(f"#tablist .tab[data-id='{d}']", button="right")
    page.wait_for_selector(".cmenu")
    assert page.locator(".cmenu [data-id=archivegroup]").count() == 0
    page.keyboard.press("Escape")
    page.click(f"#tablist .tab[data-id='{a}']", button="right")
    page.click(".cmenu [data-id=archivegroup]")
    page.wait_for_selector("#dialog:not([hidden])")
    q = page.inner_text("#dialog .q")
    assert all(
        page.inner_text(f"#tablist .tab[data-id='{k}'] .hn") in q for k in (a, b, c)
    )
    page.click("#dialog .ok")
    page.wait_for_function(
        "document.querySelectorAll('#tablist .tab[data-id]').length === 1"
    )
    assert bar(page) == [[d, []]]
    assert page.errors == []


def test_a_new_session_from_a_selection_joins_its_sources_block(server, page):  # noqa: F811
    from .test_selection_browser import REPLY, select

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "/md alpha kiwi beta")
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=new]")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.click("#sp-go")
    page.wait_for_function(
        "document.querySelectorAll('#tablist .tab[data-id]').length === 2"
    )
    [[head, ids]] = bar(page)
    assert head.startswith("grp-") and ids[0] == key and len(ids) == 2
    # The + tab afterwards starts a session of its own.
    spawn(page)
    assert len(bar(page)) == 2
    assert page.errors == []


def test_a_form_left_unsent_forgets_its_source(server, page):  # noqa: F811
    from .test_selection_browser import REPLY, select

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "/md alpha kiwi beta")
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=new]")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.goto(page.url.split("#")[0] + f"#s={key}")
    page.wait_for_selector("#a2[data-view=session]")
    page.keyboard.press("Alt+N")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.fill("#sp-text", "")
    page.click("#sp-go")
    page.wait_for_function(
        "document.querySelectorAll('#tablist .tab[data-id]').length === 2"
    )
    assert len(bar(page)) == 2
    assert page.errors == []


def test_the_group_tab_opens_its_page(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    key = group_key(page)
    name = group_name(page)
    # A quiet member leaves the bar and stays on the group's page.
    page.goto(page.url.split("#")[0] + f"#s={c}")
    page.wait_for_selector("#quiet-session[aria-pressed=false]:not([disabled])")
    page.click("#quiet-session")
    page.wait_for_function(f"!document.querySelector(\".tab[data-id='{c}']\")")
    page.goto(page.url.split("#")[0] + "#fleet")
    page.wait_for_selector("#tablist .tab.named")
    # The quiet member has no tab, but it counts in the group's +N.
    page.click(".tab.named .cnt")
    assert page.inner_text(".tab.named .cnt") == "+3"
    page.click(".tab.named .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 3")
    page.click("#tablist .tab.named .hn")
    page.wait_for_function(f"location.hash === '#g={key}'")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.wait_for_selector("#cards .card")
    ids = page.eval_on_selector_all(
        "#cards .card[data-id]", "cs => cs.map((c) => c.dataset.id)"
    )
    assert sorted(ids) == sorted([a, b, c])
    assert page.locator(f"#cards .card[data-id='{d}']").count() == 0
    assert page.is_visible(f"#cards .card.quiet[data-id='{c}']")
    assert page.inner_text(".v-fleet .order h3") == name
    assert not page.is_visible("#band")
    assert page.errors == []


def test_a_handoff_keeps_the_group(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    old = spawn(page, mcp("session_spawn", agent="opus", prompt="hello"))
    page.wait_for_function(
        "document.querySelectorAll('#tablist .tab[data-id]').length === 2"
    )
    key = group_key(page)
    [[head, ids]] = bar(page)
    assert head == key and old in ids
    kid = next(k for k in ids if k != old)
    page.fill("#input", "/ctx 342000")
    page.press("#input", "Enter")
    turns_done(page, 2)
    report(page, attention="done", line="Shipped.", handoff="Done: A. Next: B.")
    turns_done(page, 3)
    page.click("#replies .rp.hand >> text=continue fresh")
    page.wait_for_function("k => location.hash.slice(3) !== k", arg=old)
    new = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(
        "k => !document.querySelector(`#tablist .tab[data-id='${k}']`)",
        arg=old,
        timeout=8000,
    )
    # The new session joins the old group, its child still inside, nothing
    # reordered: the block keeps its key and its name.
    assert group_key(page) == key
    [[head, ids]] = bar(page)
    assert head == key
    assert new in ids and set(ids) - {new} == {kid}
    assert page.errors == []
