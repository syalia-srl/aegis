"""The tab bar (#586): bare icon buttons, the session panel's toggle in the
transcript's navigation pill, two-line tabs that keep the whole handle, a
strip that scrolls, and the menu at its end."""

import json
import re

import pytest

from .conftest import browser_slot
from .test_browser import (  # noqa: F401
    browser,
    linked,
    pick,
    playwright,
    server,
    spawn,
    turns_done,
)
from .test_browsers import site  # noqa: F401
from .test_terminal_browser import CONFIG, TermServer

pytestmark = [pytest.mark.browser, pytest.mark.slow]

HEADER_ICONS = [
    "schedules-btn",
    "settings-btn",
    "files-btn",
    "journal-btn",
    "knowledge-btn",
    "usage-btn",
]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def bare(pg, sel: str) -> dict:
    """What a header button draws around its icon."""
    return pg.eval_on_selector(
        sel,
        """b => { const c = getComputedStyle(b);
          const lbl = b.querySelector('.lbl');
          return {bg: c.backgroundColor, border: c.borderTopColor,
                  label: !!lbl && lbl.offsetWidth > 0,
                  icon: !!b.querySelector('svg') && b.querySelector('svg').getBoundingClientRect().width > 0}; }""",
    )


def test_the_header_buttons_are_bare_icons(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    for id in HEADER_ICONS:
        b = bare(page, f"#{id}")
        assert b == {
            "bg": "rgba(0, 0, 0, 0)",
            "border": "rgba(0, 0, 0, 0)",
            "label": False,
            "icon": True,
        }, id
        assert page.get_attribute(f"#{id}", "aria-label")
    assert page.errors == []


def test_the_usage_button_opens_the_usage_page(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#usage-btn")
    page.wait_for_selector("#a2[data-view=usage]")
    assert "on" in page.get_attribute("#usage-btn", "class")
    page.click("#journal-btn")
    page.wait_for_selector("#a2[data-view=journal]")
    assert "on" not in page.get_attribute("#usage-btn", "class")
    assert page.errors == []


def test_files_is_in_the_menu_on_a_phone(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    assert not pg.is_visible("#files-btn")
    pg.click("#hdr-burger")
    pg.click("#files-btn")
    pg.wait_for_selector("#a2[data-view=files]")
    pg.close()


def test_the_panel_toggle_lives_in_the_nav_pill(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    assert page.is_visible("#nav #nav-side")
    assert not page.is_visible(".tabs #side-btn")
    before = page.get_attribute("#a2", "data-side")
    page.click("#nav-side")
    side = "(document.querySelector('#a2').dataset.side ?? null)"
    page.wait_for_function(f"{side} !== {json.dumps(before)}")
    page.keyboard.press("Alt+b")
    page.wait_for_function(f"{side} === {json.dumps(before)}")
    assert page.errors == []


@pytest.mark.parametrize("touch", [True, False])
def test_a_phone_menu_holds_the_header_controls_with_tap_targets(
    server,  # noqa: F811
    browser,  # noqa: F811
    touch,
):  # noqa: F811
    # #896: the controls took a row of their own above the tabs. In the menu each
    # is a labelled row, on a touch screen and in a narrow window alike.
    pg = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=touch)
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg, "one")
    ids = ["menu-fleet", *HEADER_ICONS, "bell", "help-btn"]
    assert not pg.is_visible("#hdr-menu"), "the menu starts closed"
    assert pg.get_attribute("#hdr-burger", "aria-expanded") == "false"
    pg.click("#hdr-burger")
    assert pg.get_attribute("#hdr-burger", "aria-expanded") == "true"
    pg.wait_for_timeout(250)  # the slide
    assert pg.is_visible("#conn-dot")
    rows = pg.evaluate(
        """ids => ids.map(id => {
          const b = document.getElementById(id), r = b.getBoundingClientRect();
          return [id, r.left, r.height, getComputedStyle(b, '::after').content];
        })""",
        ids,
    )
    assert all(
        left == 0 and height >= 44 and label != "none"
        for _, left, height, label in rows
    ), rows
    assert not pg.is_visible("#side-btn")
    # Each way out closes it: a tap beside it, Escape, a pick.
    pg.mouse.click(370, 500)
    pg.wait_for_selector("#hdr-menu", state="hidden")
    pg.click("#hdr-burger")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#hdr-menu", state="hidden")
    pg.click("#hdr-burger")
    pg.click("#menu-fleet")
    pg.wait_for_selector("#hdr-menu", state="hidden")
    pg.wait_for_selector("#a2[data-view=fleet]")
    assert errors == []
    pg.close()


def test_the_menu_button_marks_a_dropped_connection(server, browser):  # noqa: F811
    # #896: the connection's state is in the closed menu, so its button says so.
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    assert "alert" not in pg.get_attribute("#hdr-burger", "class")
    server.stop()
    pg.wait_for_selector("#hdr-burger.alert")
    pg.close()


def test_a_narrow_window_keeps_the_header_on_one_row(server, browser):  # noqa: F811
    # #807: the layout must not depend on a touch screen. A 390 px window with a
    # mouse gets the phone's one row.
    pg = browser.new_page(viewport={"width": 390, "height": 600})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    box = lambda sel: pg.locator(sel).bounding_box()  # noqa: E731
    assert not pg.is_visible("#tab-fleet")
    assert abs(box("#hdr-burger")["y"] - box("#tablist")["y"]) < 8
    assert not pg.is_visible("#help-btn")
    pg.close()


def spawn_on(pg, url: str, n: int) -> None:
    pg.goto(url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    for _ in range(n):
        spawn(pg)


TABS = """() => [...document.querySelectorAll('#tablist .tab')].map(t => {
  const r = t.getBoundingClientRect(), hn = t.querySelector('.hn'), w = t.querySelector('.where');
  const wr = w && w.getBoundingClientRect();
  return {left: r.left, right: r.right,
          whole: !!hn && hn.scrollWidth <= hn.clientWidth,
          inside: !w || (wr.left >= r.left - 0.5 && wr.right <= r.right + 0.5)}; })"""


def test_fourteen_tabs_do_not_overlap_keep_whole_handles_and_scroll(
    linked,  # noqa: F811
    browser,  # noqa: F811
    page,
):
    other = browser.new_page()
    spawn_on(other, linked.beta.url, 6)
    other.close()
    spawn_on(page, linked.alpha.url, 8)
    page.wait_for_function("document.querySelectorAll('#tablist .tab').length === 14")
    tabs = page.evaluate(TABS)
    for a, b in zip(tabs, tabs[1:]):
        assert a["right"] <= b["left"] + 0.5, (a, b)
    assert all(t["whole"] for t in tabs), tabs
    assert all(t["inside"] for t in tabs), tabs
    width = "document.getElementById('tablist')"
    assert page.evaluate(f"{width}.scrollWidth > {width}.clientWidth")
    page.hover("#tablist")
    page.mouse.wheel(0, 400)
    page.wait_for_function(f"{width}.scrollLeft > 0")
    assert page.errors == []


def test_a_tab_reads_its_handle_over_its_title(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello there")
    tab = page.locator(f"#tablist .tab[data-id='{key}']")
    page.wait_for_function(
        f"document.querySelector(\"#tablist .tab[data-id='{key}'] .tt\").textContent !== ''"
    )
    handle = tab.locator(".hn").inner_text()
    assert handle and " " not in handle
    assert tab.locator(".tt").inner_text()
    assert tab.get_attribute("title") is None
    assert tab.get_attribute("aria-label").startswith(f"{handle}: ")


def test_a_long_handle_on_a_phone_keeps_the_page_its_width(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(pg)
    pg.fill("#input", "/rename extraordinarily-lengthy-handle")
    pg.tap("#send")  # on a touch screen Enter adds a line
    tab = f"#tablist .tab[data-id='{key}']"
    pg.wait_for_selector(f"{tab} .hn >> text=extraordinarily-lengthy-handle")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.locator(tab).scroll_into_view_if_needed()
    assert pg.locator(tab).is_visible()
    pg.close()


def test_the_more_button_shows_only_when_the_strip_overflows(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 900, "height": 700})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)
    assert pg.is_hidden("#tab-more")
    for _ in range(5):
        spawn(pg)
    pg.wait_for_selector("#tab-more:visible")
    assert pg.inner_text("#tab-more").strip() == "6"
    assert pg.locator('#tab-more use[href="#g-caret-d"]').count() == 1
    pg.close()


def test_the_more_button_opens_the_switcher_and_a_row_scrolls_its_tab_into_view(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    # #781: the button opens the session switcher in place of its menu.
    pg = browser.new_page(viewport={"width": 900, "height": 700})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    keys = [spawn(pg) for _ in range(6)]
    pg.goto(server.url.split("#")[0] + "#fleet")
    pg.click("#tab-more")
    pg.wait_for_selector("#switcher", state="visible")
    rows = pg.eval_on_selector_all(
        "#switcher .sw-row", "els => els.map(e => e.dataset.id)"
    )
    assert sorted(rows) == sorted(keys)
    pg.click(f"#switcher .sw-row[data-id='{keys[-1]}']")
    pg.wait_for_function(f"location.hash === '#s={keys[-1]}'")
    # The list scrolls the tab in after the hash changes: wait for it.
    pg.wait_for_function(
        f"""() => {{ const t = document.querySelector("#tablist .tab[data-id='{keys[-1]}']").getBoundingClientRect();
              const l = document.getElementById('tablist').getBoundingClientRect();
              if (t.width > l.width) return t.left <= l.left + 1 && t.right >= l.right - 1;
              return t.left >= l.left - 1 && t.right <= l.right + 1; }}""",
        timeout=5000,
    )
    pg.close()


def test_on_a_phone_the_more_button_always_shows_and_opens_the_switcher(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    # #781: a phone has no Alt key; the button is its way in, even when the
    # one tab fits, and a tap leaves the field unfocused: no keyboard.
    pg = browser.new_page(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(pg)
    pg.goto(server.url.split("#")[0] + "#fleet")
    pg.wait_for_selector("#tab-more:visible")
    # The button shows before the session's event reaches the page.
    pg.wait_for_function("document.getElementById('tab-more').innerText.trim() === '1'")
    pg.tap("#tab-more")
    pg.wait_for_selector("#switcher", state="visible")
    assert pg.evaluate("document.activeElement.id") != "switcher-q"
    box = pg.locator("#switcher .panel").bounding_box()
    assert box["width"] >= 389
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.tap(f"#switcher .sw-row[data-id='{a}']")
    pg.wait_for_function(f"location.hash === '#s={a}'")
    pg.close()


def test_a_header_mounts_into_a_given_element_and_finds_its_own_nodes(server, page):  # noqa: F811
    """b2: the header part builds its markup and looks every node up under it.

    The page already holds a header with the same ids, so this proves the part
    never asks the document: its own #tablist is a different node, and every
    reference it carries resolves inside the element it was given.
    """
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """async () => {
          const { HeaderPart } = await import('/static/js/parts/header.js');
          const { byId } = await import('/static/js/dom.js');
          const host = document.createElement('div');
          const root = document.createElement('div');
          root.className = 'aegis';
          root.dataset.theme = 'ink';
          host.append(root);
          document.body.append(host);
          const meta = { key: 'one', log_id: 'one', handle: 'one', title: 'One',
                         attention: 'idle', state: 'idle', mark: 'idle', created_at: 1 };
          const mount = { root, conn: { server: 'home' },
                          state: { ordered: [meta], sessions: new Map([['one', meta]]), links: [] } };
          const part = new HeaderPart(mount, root, {
            here: () => 'home', focusId: () => 'one', go: () => {}, open: () => {},
            openCard: () => {}, close: () => {}, move: () => {}, spawn: () => {},
            files: () => {}, side: () => {}, push: () => {}, menu: () => null,
            card: () => document.createElement('div'),
          });
          part.render(mount.state.ordered, 'one', 'session');
          const burger = byId(part.el, 'hdr-burger');
          const menu = byId(part.el, 'hdr-menu');
          const controls = burger.getAttribute('aria-controls');
          const refs = [...part.el.querySelectorAll('[aria-controls],[aria-labelledby],[for]')]
            .map((n) => n.getAttribute('aria-controls') || n.getAttribute('aria-labelledby') || n.getAttribute('for'))
            .filter(Boolean)
            .filter((id) => byId(part.el, id) === null);
          const out = {
            header: part.el.tagName === 'HEADER' && part.el.parentElement === root,
            list: byId(part.el, 'tablist') === part.el.querySelector('#tablist'),
            own: document.getElementById('tablist') !== byId(part.el, 'tablist'),
            menu: byId(part.el, 'hdr-menu') === menu,
            controls,
            names: byId(part.el, controls) === menu,
            dangling: refs,
            tabs: part.el.querySelectorAll('#tablist .tab').length,
          };
          part.destroy();
          host.remove();
          return out;
        }"""
    )
    assert got == {
        "header": True,
        "list": True,
        "own": True,
        "menu": True,
        "controls": "hdr-menu",
        "names": True,
        "dangling": [],
        "tabs": 1,
    }
    assert page.errors == []


def test_another_tab_redrawn_leaves_the_scrolled_strip_where_it_is(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    # #1094: a patch of any tab scrolled the strip back to the open one, so a
    # busy session elsewhere made the tabs out of view unreachable.
    pg = browser.new_page(viewport={"width": 900, "height": 700})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    keys = [spawn(pg) for _ in range(6)]
    strip = "document.getElementById('tablist')"
    pg.wait_for_function(f"{strip}.scrollLeft > 0")
    pg.evaluate(f"{strip}.scrollLeft = 0")
    # Count redraws in the strip, whose open tab is out of view at 0.
    pg.evaluate(
        f"""() => {{ window.patched = 0;
          new MutationObserver(() => window.patched++).observe({strip},
            {{subtree: true, childList: true, attributes: true, characterData: true}}); }}"""
    )
    other = browser.new_page(viewport={"width": 900, "height": 700})
    other.goto(server.url.split("#")[0] + f"#s={keys[0]}")
    other.wait_for_selector("#a2[data-view=session]")
    other.fill("#input", "hello")
    other.press("#input", "Enter")
    turns_done(other, 1)
    other.close()
    pg.wait_for_function("window.patched > 0")
    pg.wait_for_timeout(300)
    assert pg.evaluate(f"{strip}.scrollLeft") == 0
    # Switching tabs still brings the new one in.
    pg.goto(server.url.split("#")[0] + f"#s={keys[-1]}")
    pg.wait_for_function(f"{strip}.scrollLeft > 0")
    pg.close()


ORIENTATION = "aegis.session-tabs.orientation"
COLLAPSED = "aegis.session-tabs.collapsed"


def session_tabs(pg, orientation):
    pg.click("#settings-btn")
    chip = pg.locator("#set-session-tabs")
    chip.locator("input").click()
    chip.get_by_role("option", name=orientation, exact=True).click()
    assert chip.evaluate("chip => chip.value") == orientation.lower()


def test_session_tabs_share_the_same_list_and_order(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.is_hidden("#sessions-toggle")
    keys = []
    for total in (3, 12, 24):
        keys.extend(spawn(page) for _ in range(total - len(keys)))
        page.evaluate("window.originalTablist = document.querySelector('#tablist')")
        for orientation in ("Vertical", "Horizontal"):
            session_tabs(page, orientation)
            parent = "#a2" if orientation == "Vertical" else "header.tabs"
            assert page.locator("#tablist:visible").count() == 1
            assert page.locator(f"{parent} > #tablist").count() == 1
            assert page.evaluate(
                "originalTablist === document.querySelector('#tablist')"
            )
            assert (
                page.locator("#tablist [data-id]").evaluate_all(
                    "rows => rows.map(r => r.dataset.id)"
                )
                == keys
            )
            if orientation == "Vertical":
                assert page.locator("#tablist").bounding_box()["width"] == 232
            page.go_back()
            page.wait_for_selector(f'#tablist .on[data-id="{keys[-1]}"]')
            assert page.evaluate("location.hash") == f"#s={keys[-1]}"
    assert page.errors == []


@pytest.mark.parametrize("fault", ["invalid", "read", "write"])
def test_session_tab_preference_scoped_fallback(server, page, fault):  # noqa: F811
    page.add_init_script(
        f"""(() => {{
            const keys = {json.dumps([ORIENTATION, COLLAPSED])};
            const fault = {json.dumps(fault)};
            if (fault === "invalid") {{
              keys.forEach(k => localStorage.setItem(k, "invalid"));
              return;
            }}
            if (fault === "read") {{
              localStorage.setItem(keys[0], "vertical");
              localStorage.setItem(keys[1], "true");
            }}
            const method = fault === "read" ? "getItem" : "setItem";
            const original = Storage.prototype[method];
            Storage.prototype[method] = function(key, ...rest) {{
              if (keys.includes(key)) throw new Error("scoped storage failure");
              return original.call(this, key, ...rest);
            }};
        }})()"""
    )
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.is_hidden("#sessions-toggle")
    key = spawn(page)
    page.evaluate(
        "localStorage.setItem('aegis.tab-folds', JSON.stringify(['grp-saved']))"
    )
    session_tabs(page, "Vertical")
    toggle = page.get_by_role("button", name="Collapse sessions")
    toggle.click()
    page.get_by_role("button", name="Show sessions").click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.locator(f'#tablist [data-id="{key}"]').count() == 1
    assert page.evaluate("JSON.parse(localStorage.getItem('aegis.tabs'))") == [key]
    assert page.evaluate("JSON.parse(localStorage.getItem('aegis.tab-folds'))") == [
        "grp-saved"
    ]
    if fault == "write":
        page.reload()
        page.wait_for_selector("#set-session-tabs")
        assert page.is_hidden("#sessions-toggle")
        assert (
            page.locator("#set-session-tabs").evaluate("chip => chip.value")
            == "horizontal"
        )
    assert page.errors == []


def test_session_tabs_restore_across_reload_resize_and_switcher(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    keys = [spawn(page) for _ in range(2)]
    session_tabs(page, "Vertical")
    collapse = page.get_by_role("button", name="Collapse sessions")
    reopen = page.get_by_role("button", name="Show sessions")
    for collapsed in (False, True):
        if collapsed:
            collapse.click()
        page.reload()
        (reopen if collapsed else collapse).wait_for()
        with page.expect_popup() as other_page:
            page.evaluate("window.open('/')")
        other = other_page.value
        other.get_by_role(
            "button", name="Show sessions" if collapsed else "Collapse sessions"
        ).wait_for()
        other.close()
        toggle = reopen if collapsed else collapse
        toggle.focus()
        page.set_viewport_size({"width": 761, "height": 800})
        assert toggle.evaluate("b => b === document.activeElement")
        page.set_viewport_size({"width": 760, "height": 800})
        page.wait_for_function("document.activeElement.id === 'hdr-burger'")
        assert page.get_attribute("#hdr-burger", "aria-expanded") == "false"
        page.set_viewport_size({"width": 390, "height": 800})
        assert page.is_visible("header.tabs > #tablist")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        page.set_viewport_size({"width": 761, "height": 800})
        page.wait_for_function("document.activeElement.id === 'sessions-toggle'")
        assert toggle.get_attribute("aria-expanded") == str(not collapsed).lower()
    page.click("#tab-more")
    page.click(f'#switcher [data-id="{keys[0]}"]')
    page.wait_for_function("location.hash === " + json.dumps(f"#s={keys[0]}"))
    assert reopen.is_visible()
    page.keyboard.press("Alt+p")
    page.click(f'#switcher [data-id="{keys[1]}"]')
    page.wait_for_function("location.hash === " + json.dumps(f"#s={keys[1]}"))
    reopen.focus()
    reopen.press("Space")
    assert collapse.evaluate("b => b === document.activeElement")
    assert page.locator(f'#tablist .on[data-id="{keys[1]}"]').is_visible()
    session_tabs(page, "Horizontal")
    page.reload()
    page.wait_for_selector("#set-session-tabs")
    page.set_viewport_size({"width": 760, "height": 800})
    page.locator("#hdr-burger").focus()
    page.set_viewport_size({"width": 761, "height": 800})
    page.wait_for_function("document.activeElement.id === 'tab-fleet'")
    assert page.errors == []


def test_same_origin_embeds_ignore_the_standalone_session_tab_settings(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page)
    session_tabs(page, "Vertical")
    page.get_by_role("button", name="Collapse sessions").click()
    host = f"""<!doctype html><title>Same-origin host</title>
      <div id="full" style="height:300px"></div><div id="chat" style="height:150px"></div>
      <div id="tabs" style="height:150px"></div>
      <script type="module">
        import {{ mount }} from '/static/js/mount.js';
        const getItem = Storage.prototype.getItem;
        window.preferenceReads = [];
        Storage.prototype.getItem = function(key) {{
          if ({json.dumps([ORIENTATION, COLLAPSED])}.includes(key)) {{
            preferenceReads.push(key);
          }}
          return getItem.call(this, key);
        }};
        for (const layout of ["full", "chat", "tabs"]) {{
          mount(document.getElementById(layout), {{ layout, session: {json.dumps(key)} }});
        }}
      </script>"""
    page.evaluate(
        """html => {
      const frame = document.createElement('iframe');
      frame.id = 'embedded-test';
      frame.style = 'position:fixed;inset:0;width:100%;height:100%;z-index:100';
      frame.srcdoc = html;
      document.body.append(frame);
    }""",
        host,
    )
    frame = page.frame_locator("#embedded-test")
    frame.locator("#full .aegis[data-view=session] header > #tablist .tab").wait_for()
    frame.locator("#chat .aegis[data-layout=chat] textarea#input").wait_for()
    frame.locator(
        "#tabs .aegis[data-layout=tabs] .aegis-tab:not(.aegis-tab-new)"
    ).wait_for()
    assert frame.locator("#full #sessions-toggle").is_hidden()
    frame.locator("#full #settings-btn").click()
    frame.locator("#full #set-browser").wait_for()
    assert frame.locator("#set-session-tabs").count() == 0
    assert frame.locator("body").evaluate("() => preferenceReads") == []
    assert page.errors == []


def test_session_tab_focus_follows_chromium_page_zoom(server, tmp_path):  # noqa: F811
    extension = tmp_path / "zoom"
    extension.mkdir()
    (extension / "manifest.json").write_text(
        json.dumps(
            {
                "name": "Browser zoom fixture",
                "version": "1.0",
                "manifest_version": 3,
                "background": {"service_worker": "zoom.js"},
                "permissions": ["tabs"],
            }
        )
    )
    (extension / "zoom.js").write_text(
        "chrome.runtime.onInstalled.addListener(() => {});"
    )
    with browser_slot(), playwright.sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            tmp_path / "profile",
            channel="chromium",
            no_viewport=True,
            args=[
                "--window-size=820,850",
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}",
            ],
        )
        try:
            tab = context.pages[0]
            worker = (
                context.service_workers[0]
                if context.service_workers
                else context.wait_for_event("serviceworker")
            )
            tab.goto(server.url)
            tab.wait_for_selector("#a2[data-view=fleet]")
            session_tabs(tab, "Vertical")
            tab.locator("#sessions-toggle").focus()
            assert tab.evaluate("innerWidth") > 760
            worker.evaluate(
                """async zoom => {
                const [tab] = await chrome.tabs.query({active: true});
                await chrome.tabs.setZoom(tab.id, zoom);
              }""",
                1.25,
            )
            tab.wait_for_function(
                "innerWidth <= 760 && document.activeElement.id === 'hdr-burger'"
            )
            assert tab.get_attribute("#hdr-burger", "aria-expanded") == "false"
            worker.evaluate("""async () => {
                const [tab] = await chrome.tabs.query({active: true});
                await chrome.tabs.setZoom(tab.id, 1);
              }""")
            tab.wait_for_function(
                "innerWidth > 760 && document.activeElement.id === 'sessions-toggle'"
            )
        finally:
            context.close()


@pytest.fixture
def row_server(tmp_path, fake_claude, fake_opencode):
    profile = "terminal-profile-with-a-name-too-wide-to-fit-in-the-session-list"
    (tmp_path / ".aegis.yaml").write_text(
        CONFIG + f"terminals:\n  {profile}: {{cmd: sh, cwd: .}}\n"
    )
    served = TermServer(tmp_path, fake_claude, fake_opencode).start()
    yield served, profile
    served.stop()


def test_session_tab_names_themes_and_touch(row_server, site, browser):  # noqa: F811
    served, profile = row_server
    tab = browser.new_page(
        viewport={"width": 1280, "height": 800}, has_touch=True, reduced_motion="reduce"
    )
    errors = []
    asset_errors = []
    tab.on("pageerror", lambda err: errors.append(str(err)))
    tab.on(
        "requestfailed",
        lambda r: asset_errors.append(r.url) if "/static/" in r.url else None,
    )
    tab.on(
        "response",
        lambda r: (
            asset_errors.append(r.url)
            if "/static/" in r.url and r.status >= 400
            else None
        ),
    )
    tab.goto(served.url)
    tab.wait_for_selector("#a2[data-view=fleet]")
    session_tabs(tab, "Vertical")
    assert tab.locator("#tablist .tab").count() == 0
    handle = "extraordinary-longnavigation-accessibility"
    title = "A long session title that stays available as an accessible name"
    key = spawn(
        tab, "/mcp session_rename " + json.dumps({"handle": handle, "title": title})
    )
    terminal = served.call("term.spawn", {"profile": profile})
    web = served.call("browser.spawn", {"url": site})
    session = tab.locator(f'#tablist [data-id="{key}"]')
    term = tab.locator(f'#tablist [data-id="{terminal["id"]}"]')
    web_row = tab.locator(f'#tablist [data-id="{web["id"]}"]')
    playwright.expect(session).to_have_accessible_name(
        re.compile(re.escape(f"{handle}: {title} ("))
    )
    assert term.get_attribute("aria-label") == f"Terminal: {terminal['name']} (running)"
    assert (
        web_row.get_attribute("aria-label") == f"Browser: {web['name']} (you hold it)"
    )
    assert session.locator(".hn").evaluate("n => n.scrollWidth > n.clientWidth")
    assert term.locator(".tname").evaluate("n => n.scrollWidth > n.clientWidth")
    assert term.locator("use[href='#g-terminal']").count() == 1
    for orientation in ("Horizontal", "Vertical"):
        session_tabs(tab, orientation)
        term.press("Enter")
        tab.wait_for_selector("#a2[data-view=term] .xterm-rows")
        assert tab.evaluate("location.hash") == f"#t={terminal['id']}"
        web_row.press("Space")
        tab.wait_for_selector("#a2[data-view=browser] .br-screen")
        tab.wait_for_selector(
            "#a2[data-view=browser] .br-wait", state="hidden", timeout=15_000
        )
        assert tab.evaluate("location.hash") == f"#b={web['id']}"
    tab.click("#settings-btn")
    themes = tab.locator("#set-theme").evaluate(
        "chip => chip.options.map(option => option.label)"
    )
    assert len(themes) == 10
    for theme in themes:
        pick(tab, "#set-theme", theme)
        assert tab.locator("#a2").get_attribute("data-theme") == theme.lower()
        assert tab.locator("#sessions-toggle use[href='#g-panel']").count() == 1
        assert tab.locator("#tablist").bounding_box()["width"] == 232
    origin = tab.evaluate("location.origin")
    tab.goto(f"{origin}/?lang=es#s={key}")
    tab.get_by_role("button", name="Contraer sesiones").wait_for()
    assert session.get_attribute("aria-label").startswith(f"{handle}: {title} (")
    assert (
        term.get_attribute("aria-label") == f"Terminal: {terminal['name']} (corriendo)"
    )
    assert (
        web_row.get_attribute("aria-label")
        == f"Navegador: {web['name']} (la tienes tú)"
    )

    # A focused row follows the list into the phone strip, then yields focus
    # to Sessions when the desktop list is still folded.
    term.focus()
    tab.locator("#sessions-toggle").evaluate("b => b.click()")
    assert tab.evaluate("document.activeElement.id") == "sessions-toggle"
    assert (
        tab.get_by_role(
            "button", name=f"Terminal: {terminal['name']} (corriendo)"
        ).count()
        == 0
    )
    tab.set_viewport_size({"width": 760, "height": 800})
    tab.wait_for_function("document.activeElement.id === 'hdr-burger'")
    term.focus()
    tab.set_viewport_size({"width": 761, "height": 800})
    tab.wait_for_function("document.activeElement.id === 'sessions-toggle'")
    tab.locator("#sessions-toggle").press("Enter")
    term.focus()
    tab.set_viewport_size({"width": 760, "height": 800})
    assert term.evaluate("row => row === document.activeElement")
    tab.locator("#input").focus()
    tab.set_viewport_size({"width": 390, "height": 800})
    assert tab.evaluate("document.activeElement.id") == "input"
    assert tab.evaluate("document.documentElement.scrollWidth === innerWidth")
    assert tab.locator("#input").evaluate("""el => {
        const box = el.getBoundingClientRect();
        return document.elementFromPoint(box.x + 5, box.y + 5) === el;
    }""")
    tab.set_viewport_size({"width": 1280, "height": 800})
    session.scroll_into_view_if_needed()
    box = session.bounding_box()
    touch = tab.context.new_cdp_session(tab)
    touch.send(
        "Input.dispatchTouchEvent",
        {
            "type": "touchStart",
            "touchPoints": [{"x": box["x"] + 20, "y": box["y"] + 15}],
        },
    )
    tab.locator(".tabcard.tabsheet").wait_for()
    touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    assert tab.locator(".tabcard.tabsheet .acts button").count() > 0
    assert tab.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert errors == [] and asset_errors == []
    tab.close()
