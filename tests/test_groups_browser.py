"""Groups in the client (#586): groups.js's blocks, and the bar that draws them."""

import pytest

from .test_agents import mcp
from .test_browser import (  # noqa: F401
    browser,
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


def popped(pg, metas, key, folded=True) -> list:
    """The member keys poppedOut() draws beside the block of `key`."""
    return pg.evaluate(
        """async ([metas, key, folded]) => {
          const g = await import('/static/js/groups.js');
          const b = g.blocksOf(metas, 'alpha').find((x) => x.key === key);
          return g.poppedOut(b, folded).map((m) => m.key);
        }""",
        [metas, key, folded],
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
    browser = m("x", 6, g, kind="browser")
    assert blocks(page, [a, b, one, lone, term, browser]) == [
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
    assert page.errors == []


def slash_spawn(pg, key: str) -> str:
    """A person's /spawn typed in session `key`: the new session's key."""
    pg.goto(pg.url.split("#")[0] + f"#s={key}")
    pg.wait_for_selector(f".tab.on[data-id='{key}']")
    before = set(
        pg.eval_on_selector_all("#tablist .tab", "ts => ts.map(t => t.dataset.id)")
    )
    pg.fill("#input", "/spawn opus")
    pg.press("#input", "Enter")
    pg.wait_for_function(
        f"document.querySelectorAll('#tablist .tab').length === {len(before) + 1}"
    )
    after = pg.eval_on_selector_all("#tablist .tab", "ts => ts.map(t => t.dataset.id)")
    return next(k for k in after if k not in before)


def bar(pg) -> list:
    """The bar as [[tab keys of a block], ...]."""
    return pg.eval_on_selector_all(
        "#tablist .tblock",
        "bs => bs.map(b => [...b.querySelectorAll('.tab')].map(t => t.dataset.id))",
    )


def three_and_one(pg, srv):
    pg.goto(srv.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(pg, "hello")
    b = slash_spawn(pg, a)
    c = slash_spawn(pg, a)
    d = spawn(pg)
    return a, b, c, d


def test_a_session_spawned_from_another_joins_its_block(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    assert bar(page) == [[a, b, c], [d]]
    assert page.is_visible(f".tblock.grp .tab.head[data-id='{a}']")
    assert page.locator(".tblock.grp").count() == 1
    assert page.errors == []


def test_folding_hides_members_and_survives_a_reload(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    page.click(f".tab.head[data-id='{a}'] .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    assert page.inner_text(f".tab.head[data-id='{a}'] .cnt") == "+2"
    page.reload()
    page.wait_for_selector(f".tab.head[data-id='{a}']")
    assert bar(page) == [[a], [d]]
    page.click(f".tab.head[data-id='{a}'] .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 3")
    assert page.errors == []


def drop(pg, src: str, dst: str, side: str) -> None:
    box = pg.locator(f"#tablist .tab[data-id='{dst}']").bounding_box()
    x = 3 if side == "left" else box["width"] - 3
    pg.drag_and_drop(
        f"#tablist .tab[data-id='{src}']",
        f"#tablist .tab[data-id='{dst}']",
        target_position={"x": x, "y": box["height"] / 2},
    )


def test_a_head_drags_its_group_and_a_member_stays_inside(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    drop(page, d, a, "left")
    page.wait_for_function(
        f"document.querySelector('#tablist .tab').dataset.id === {d!r}"
    )
    assert bar(page) == [[d], [a, b, c]]
    drop(page, b, d, "left")  # out of its group: refused
    assert bar(page) == [[d], [a, b, c]]
    drop(page, b, b, "left")  # on itself: it stays
    assert bar(page) == [[d], [a, b, c]]
    drop(page, c, b, "left")
    page.wait_for_function(
        f"document.querySelectorAll('.tblock.grp .tab')[1].dataset.id === {c!r}"
    )
    assert bar(page) == [[d], [a, c, b]]
    drop(page, a, d, "left")
    page.wait_for_function(
        f"document.querySelector('#tablist .tab').dataset.id === {a!r}"
    )
    assert bar(page) == [[a, c, b], [d]]
    assert page.errors == []


def test_alt_shift_brackets_move_the_tab_in_front_as_a_drag_does(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)

    def press(key: str, at: str, want: list, box: bool = False) -> None:
        page.click(f"#tablist .tab[data-id='{at}']")
        page.wait_for_selector(f"#tablist .tab.on[data-id='{at}']")
        if box:
            page.focus("#input")
        page.keyboard.press(f"Alt+Shift+{key}")
        page.wait_for_function(
            """want => JSON.stringify([...document.querySelectorAll('#tablist .tblock')]
                 .map(b => [...b.querySelectorAll('.tab')].map(t => t.dataset.id)))
               === JSON.stringify(want)""",
            arg=want,
        )

    press("BracketLeft", d, [[d], [a, b, c]])  # a lone tab passes a whole group
    press("BracketLeft", d, [[d], [a, b, c]])  # the first stays
    press("BracketLeft", c, [[d], [a, c, b]])  # a member, within its group
    press("BracketLeft", c, [[d], [a, c, b]])  # never past its head
    press("BracketRight", c, [[d], [a, b, c]])
    press("BracketRight", c, [[d], [a, b, c]])  # nor out of its group
    press("BracketRight", a, [[d], [a, b, c]])  # the last block stays
    press("BracketLeft", a, [[a, b, c], [d]], box=True)  # a head, its group along
    assert page.input_value("#input") == ""  # the chord typed nothing
    page.reload()
    page.wait_for_selector("#tablist .tblock")
    assert bar(page) == [[a, b, c], [d]]
    assert page.errors == []


def test_alt_j_unfolds_a_group_to_reach_a_hidden_member(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    page.click(f"#tablist .tab[data-id='{c}']")
    page.evaluate("document.hasFocus = () => false")
    report(page, attention="needs_you", line="Which build?", replies=[])
    turns_done(page, 1)
    page.click(f"#tablist .tab[data-id='{d}']")
    page.evaluate("delete document.hasFocus")
    page.click(f".tab.head[data-id='{a}'] .cnt")
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    page.keyboard.press("Alt+j")
    page.wait_for_function(f"location.hash === '#s={c}'")
    page.wait_for_selector(f"#tablist .tab.on[data-id='{c}']")
    assert bar(page)[0] == [a, b, c]  # the group unfolded to show it
    assert page.errors == []


def test_a_heads_menu_archives_its_group(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    page.click(f"#tablist .tab[data-id='{b}']", button="right")
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
    page.wait_for_function("document.querySelectorAll('#tablist .tab').length === 1")
    assert bar(page) == [[d]]
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
    page.wait_for_function("document.querySelectorAll('#tablist .tab').length === 2")
    assert len(bar(page)) == 1 and bar(page)[0][0] == key
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
    page.wait_for_function("document.querySelectorAll('#tablist .tab').length === 2")
    assert len(bar(page)) == 2
    assert page.errors == []


def test_a_handoff_takes_its_place_at_the_head_of_its_group(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    old = spawn(page, mcp("session_spawn", agent="opus", prompt="hello"))
    page.wait_for_function("document.querySelectorAll('#tablist .tab').length === 2")
    [[head, kid]] = bar(page)
    assert head == old
    page.fill("#input", "/ctx 342000")
    page.press("#input", "Enter")
    turns_done(page, 2)
    report(page, attention="done", line="Shipped.", handoff="Done: A. Next: B.")
    turns_done(page, 3)
    page.click("#replies .rp.hand >> text=continue fresh")
    page.wait_for_function("k => location.hash.slice(3) !== k", arg=old)
    new = page.evaluate("location.hash.slice(3)")
    page.wait_for_selector(".row >> text=you head it now")
    page.wait_for_function(
        "k => !document.querySelector(`#tablist .tab[data-id='${k}']`)",
        arg=old,
        timeout=8000,
    )
    # The new tab heads the old group, ahead of the older child.
    assert bar(page) == [[new, kid]]
    assert page.errors == []
