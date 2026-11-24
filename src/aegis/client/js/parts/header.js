// The tab strip and the header: the whole <header class="tabs">, from the
// burger and the phone's menu to the connection dot, the view buttons, the
// bell and the session switcher the overflow button opens.
//
// The part builds its own markup (the slice index.html used to hold), finds
// every node under the element it built and never asks the page for an id. The
// tab list itself is tabs.js's: renderTabs and patchTab take the list, the
// metas and the actions, and the part supplies all three. What it cannot know
// — how to open a session, where a view goes, how a Fleet card is drawn — the
// page hands it as `actions`, until the full layout owns them (b8).
//
// Its state is the strip's own: the folded groups in this browser, the quiet
// flag each tab was last drawn with, and the switcher. The view marks (which
// header button is on) follow the route the page reports.

import { byId } from "../dom.js";
import { applyLabels, labelsFor } from "../labels.js";
import { icon } from "../glyphs.js";
import { attach as attachMenu } from "../menu.js";
import { installBell } from "../ping.js";
import { pushSupported } from "../pushpanel.js";
import { attachTabCard } from "../tabcard.js";
import { cancelTabDrag, patchTab, renderTabs, stepMove } from "../tabs.js";
import { Switcher } from "../switcher.js";

// The markup this part owns. The ids stay as they are: they are the browser
// suite's hooks, and two mounts' copies are harmless because every lookup is
// scoped to the part's own element.
const HEADER_HTML = `
<button class="hdr-burger" id="hdr-burger" data-t-title="header.menu" data-t-aria="header.menu" aria-expanded="false" aria-controls="hdr-menu"></button>
<button class="sessions-toggle keys-btn" id="sessions-toggle" type="button" aria-controls="tablist" aria-expanded="false" hidden><span data-t="header.sessions"></span></button>
<button class="tab home" id="tab-fleet" type="button" data-t-title="header.fleetTitle" data-t="header.fleet"></button>
<div class="tablist" id="tablist"></div>
<button class="tab-more" id="tab-more" data-t-title="header.moreTitle" data-t-aria="header.more" aria-haspopup="menu" hidden></button>
<button class="new-session needs-net" id="tab-add" type="button" data-t-title="header.newTitle" data-t-aria="header.new"><span class="plus">+</span><span class="lbl" data-t="header.new"></span></button>
<div class="gap"></div>
<div class="hdr-scrim" id="hdr-scrim"></div>
<div class="hdr-menu" id="hdr-menu">
<div class="servers"><span class="dot ghost" id="conn-dot"></span><span id="conn-text" data-t="header.connecting"></span><span class="ver-top" id="ver-top" hidden></span></div>
<button class="menu-fleet" id="menu-fleet" data-t-title="header.fleetTitle" data-t-aria="header.fleet"></button>
<button class="keys-btn set-btn sch-btn" id="schedules-btn" data-t-title="header.schedulesTitle" data-t-aria="header.schedules"><span class="lbl" data-t="header.schedules"></span><span class="n" id="schedules-n"></span></button>
<button class="keys-btn set-btn" id="models-btn" title="Models" aria-label="Models"><span class="lbl">Models</span></button>
<button class="keys-btn set-btn" id="settings-btn" data-t-title="header.settingsTitle" data-t-aria="header.settings"><span class="lbl" data-t="header.settings"></span></button>
<button class="jr-btn" id="files-btn" data-t-title="header.filesTitle" data-t-aria="header.files"></button>
<button class="jr-btn" id="journal-btn" data-t-title="header.journalTitle" data-t-aria="header.journal"></button>
<button class="jr-btn" id="knowledge-btn" data-t-title="header.knowledgeTitle" data-t-aria="header.knowledge"></button>
<button class="jr-btn" id="usage-btn" data-t-title="header.usageTitle" data-t-aria="header.usage"></button>
<button class="keys-btn bell" id="bell" data-t-title="header.bellTitle" data-t-aria="header.notifications"></button>
<button class="side-btn" id="side-btn" data-t-title="header.sideTitle" data-t-aria="header.side"></button>
<button class="keys-btn help-btn" id="help-btn" data-t-title="header.helpTitle" data-t-aria="header.help"><span class="q">?</span></button>
</div>
<div class="palette switcher" id="switcher" hidden></div>`;

// `actions` is what the page knows and the part must not: open(key) opens a
// session, openCard(key) opens a quiet one through its Fleet card, close(key)
// archives one, move(keys, before) reorders the tabs, and go(hash) navigates.
// `focusId()` is the session in view, `here()` this server's name, and
// `menu(key)`/`card(m)` the tab card's items and its Fleet card. `spawn()`,
// `files()`, `side()` and `push(bell, anchor)` are the header's own buttons.
export class HeaderPart {
  constructor(mount, host, actions = {}) {
    this.mount = mount;
    this.t = mount.t || labelsFor("en");
    this.act = actions;
    this.root = mount.root || host;
    this.quietSeen = new Map(); // key -> whether it was quiet when last drawn
    this.tabFolds = new Set(JSON.parse(localStorage.getItem("aegis.tab-folds") || "[]"));
    this.renaming = null; // the group key whose name is an input (#1039)
    this.phone = matchMedia("(max-width:760px)"); // the one-row header (client.css)
    this.coarse = matchMedia("(pointer:coarse)");

    this.el = document.createElement("header");
    this.el.className = "tabs";
    this.el.innerHTML = HEADER_HTML;
    applyLabels(this.el, this.t);
    host.append(this.el);

    const n = (id) => byId(this.el, id);
    this.burger = n("hdr-burger");
    this.fleetTab = n("tab-fleet");
    this.sessionsToggle = n("sessions-toggle");
    this.list = n("tablist");
    this.more = n("tab-more");
    this.add = n("tab-add");
    this.scrim = n("hdr-scrim");
    this.menu = n("hdr-menu");
    this.connDot = n("conn-dot");
    this.connText = n("conn-text");
    this.menuFleet = n("menu-fleet");
    this.schedulesBtn = n("schedules-btn");
    this.modelsBtn = n("models-btn");
    this.schedulesN = n("schedules-n");
    this.settingsBtn = n("settings-btn");
    this.filesBtn = n("files-btn");
    this.journalBtn = n("journal-btn");
    this.knowledgeBtn = n("knowledge-btn");
    this.usageBtn = n("usage-btn");
    this.bell = n("bell");
    this.sideBtn = n("side-btn");
    this.helpBtn = n("help-btn");
    this.switcherBox = n("switcher");

    const part = this;
    // What tabs.js calls while it draws: the bar's actions are the page's,
    // except the folds, which belong to this browser and this part.
    this.tabActions = {
      t: this.t,
      onFocus: (key) => part.act.open?.(key),
      onClose: (key) => part.act.close?.(key),
      onMove: (keys, before) => part.act.move?.(keys, before),
      onGroup: (key) => part.act.onGroup?.(key),
      onJoin: (keys, group) => part.act.join?.(keys, group),
      onLeave: (keys) => part.act.leave?.(keys),
      // Rename in place (#1039): the part holds which group is an input, since
      // the strip it draws is this part's own.
      get renaming() {
        return part.renaming;
      },
      onRenameStart: (key) => part.startRename(key),
      onRenameSave: (key, name) => part.saveRename(key, name),
      onRenameCancel: () => part.cancelRename(),
      get here() {
        return part.act.here?.() || "";
      },
      get group() {
        return part.act.groupKey?.() || null;
      },
      // Every open session, quiet ones too: a quiet member has no tab but
      // still counts in its group's +N (#1039).
      get all() {
        return part.mount.state.ordered;
      },
      folds: this.tabFolds,
      saveFolds: () => localStorage.setItem("aegis.tab-folds", JSON.stringify([...part.tabFolds])),
      onFold: (key) => {
        if (!part.tabFolds.delete(key)) part.tabFolds.add(key);
        part.tabActions.saveFolds();
        part.render(part.tabs(), part.current());
      },
    };

    // The session switcher (#781): Alt+P, or the strip's overflow button, lists
    // every open session (quiet ones last) and opens one through openCard so a
    // quiet session gets its tab back.
    this.switcher = new Switcher(this.switcherBox, {
      t: this.t,
      metas: () => this.mount.state.ordered,
      here: () => this.act.here?.() || this.t("sidebar.thisServer"),
      links: () => this.mount.state.links,
      current: () => this.current(),
      open: (key) => (this.act.openCard || this.act.open)?.(key),
    });

    // The tab card (#586): a session tab's Fleet card, on hover or long press.
    this.tabCard = attachTabCard(this.list, {
      meta: (key) => this.mount.state.sessions.get(key),
      card: (m) => this.act.card?.(m),
      items: (key) => this.act.menu?.(key),
      place: (box, row) => {
        // In the left list, a card below its row would cover the next rows.
        const left = this.vertical ? this.list.getBoundingClientRect().right + 6 : row.left;
        const top = this.vertical ? Math.max(8, Math.min(row.top, innerHeight - box.offsetHeight - 8)) : row.bottom + 6;
        box.style.left = `${Math.max(8, Math.min(left, innerWidth - box.offsetWidth - 8))}px`;
        box.style.top = `${top}px`;
      },
    });
    attachMenu(
      this.list,
      (t) => {
        if (t?.dataset?.id && t.matches(".tab[data-id]")) return this.act.menu?.(t.dataset.id);
        if (t?.dataset?.group && t.matches(".tab.named")) return this.act.groupMenu?.(t.dataset.group);
        return null;
      },
      {
        selector: ".tab[data-id], .tab.named[data-group]",
        touch: (t) => t.matches(".tab[data-id]") && this.tabCard.sheet(t),
      },
    );

    this.onHideMenu = () => this.setMenu(false);
    this.onPhone = () => {
      this.placeTabs();
      this.setMenu(false);
    };
    this.phone.addEventListener("change", this.onPhone);

    this.icons();
    this.wire();
    // The header's height, for the drawer to start under it: it wraps to two
    // rows on a phone and grows with the safe area.
    this.headerObserver = new ResizeObserver(() =>
      this.root.style.setProperty("--hdr", `${this.el.getBoundingClientRect().height}px`),
    );
    this.headerObserver.observe(this.el);
    // The strip's overflow button counts every open session, quiet ones too.
    // It also fires when a tab redraws: only a new width forces the focus.
    this.listObserver = new ResizeObserver(([e]) => {
      this.size();
      const extent = this.vertical ? e.contentRect.height : e.contentRect.width;
      this.focus(extent !== this.extent);
      this.extent = extent;
    });
    this.listObserver.observe(this.list);
    this.mutationObserver = new MutationObserver(() => this.size());
    this.mutationObserver.observe(this.list, { childList: true });
    this.placeTabs();
  }

  // A part draws its own glyphs; the sprite is in the mount's root (glyphs.js
  // installed it there).
  icons() {
    this.burger.append(icon("menu"));
    this.sessionsToggle.prepend(icon("panel"));
    this.menuFleet.append(icon("fleet"));
    this.bell.append(icon("bell"));
    this.settingsBtn.prepend(icon("gear"));
    this.modelsBtn.prepend(icon("models"));
    this.journalBtn.append(icon("book"));
    this.knowledgeBtn.append(icon("knowledge"));
    this.usageBtn.append(icon("usage"));
    this.filesBtn.append(icon("folder"));
    this.schedulesBtn.prepend(icon("clock"));
    this.sideBtn.append(icon("panel"));
  }

  wire() {
    // The Fleet tab, the menu's, New session and the views.
    this.fleetTab.addEventListener("click", () => this.act.go?.("#fleet"));
    this.menuFleet.addEventListener("click", () => this.act.go?.("#fleet"));
    this.sessionsToggle.addEventListener("click", () => this.act.sessionTabs.toggle());
    this.add.addEventListener("click", () => this.act.spawn?.());
    byId(this.el, "models-btn").addEventListener("click", () => this.act.go?.("#models"));
    this.settingsBtn.addEventListener("click", () => this.act.go?.("#settings"));
    this.journalBtn.addEventListener("click", () => this.act.go?.("#journal"));
    this.knowledgeBtn.addEventListener("click", () => this.act.go?.("#knowledge"));
    this.usageBtn.addEventListener("click", () => this.act.go?.("#usage"));
    this.schedulesBtn.addEventListener("click", () => this.act.go?.("#schedules"));
    this.filesBtn.addEventListener("click", () => this.act.files?.());
    this.helpBtn.addEventListener("click", () => this.act.go?.("#help"));
    this.sideBtn.addEventListener("click", () => this.act.side?.());
    // On a phone the bell closes its menu, so the panel hangs from the menu
    // button.
    installBell(this.bell, pushSupported() ? (b) => this.act.push?.(b, this.burger) : null);

    // The overflow button opens the switcher; a phone tap leaves the field
    // unfocused, so no keyboard covers the list.
    this.more.addEventListener("click", () => this.switcher.open({ focus: !this.coarse.matches }));

    // The phone's menu (#896): the controls a one-row header has no room for.
    // A pick, a tap beside it, Escape or another view closes it.
    this.burger.addEventListener("click", () => {
      this.setMenu(true);
      this.menuFleet.focus({ preventScroll: true });
    });
    this.scrim.addEventListener("click", () => this.setMenu(false));
    this.menu.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) this.setMenu(false);
    });
    window.addEventListener("hashchange", this.onHideMenu);
  }

  placeTabs() {
    const prefs = this.act.sessionTabs;
    if (!prefs) {
      this.size();
      return;
    }
    const phone = this.phone.matches;
    const hasHeader = prefs.hasHeader;
    const vertical = !phone && hasHeader && prefs.orientation === "vertical";
    const collapsed = vertical && prefs.collapsed;
    // Update these controls in JS so media-query CSS cannot blur them first.
    const active = document.activeElement;
    const inList = this.list.contains(active);
    const moving = vertical !== this.vertical;
    const changed = moving || collapsed !== this.collapsed;
    if (changed || String(phone) !== this.el.dataset.phone) cancelTabDrag(this.list);
    this.el.dataset.phone = String(phone);
    if (changed) {
      this.tabCard.hide();
      this.vertical = vertical;
      this.collapsed = collapsed;
      this.root.classList.toggle("sessions-left", vertical && !collapsed);
      this.list.hidden = collapsed;
      if (vertical && moving) this.root.insertBefore(this.list, this.root.querySelector("main.view"));
      else if (!vertical && this.list.parentElement !== this.el) this.el.insertBefore(this.list, this.more);
      this.list.classList.toggle("vertical", vertical);
      this.sessionsToggle.hidden = !vertical;
      this.sessionsToggle.setAttribute("aria-expanded", String(vertical && !collapsed));
      const action = this.t(collapsed ? "header.showSessions" : "header.collapseSessions");
      this.sessionsToggle.setAttribute("aria-label", action);
      this.sessionsToggle.title = action;
      this.focus(true);
    }
    this.size();
    if (!hasHeader) return;
    const fallback = phone ? this.burger : vertical ? this.sessionsToggle : this.fleetTab;
    if ((inList && collapsed) || (active === this.sessionsToggle && !vertical) ||
        (active === this.burger && !phone) || (active === this.fleetTab && phone)) {
      fallback.focus({ preventScroll: true });
    } else if (inList && moving && active !== document.activeElement) {
      active.focus({ preventScroll: true });
    }
  }

  tabs() {
    return this.mount.state.ordered.filter((m) => !m.quiet);
  }

  current() {
    return this.act.focusId?.() ?? null;
  }

  // -- what the page draws through the part ---------------------------------
  // A page redraws the whole strip when the set of tabs changes or a group
  // does; `focusId` is the session in view and `view` names the route, for the
  // header buttons' on marks.
  render(metas, focusId = null, view = undefined) {
    renderTabs(this.list, metas, focusId, this.tabActions);
    if (view !== undefined) this.mark(view);
    // The count button may show and narrow the strip: size it, then focus.
    this.size();
    this.focus();
  }

  // One session's tab redrawn where it stands. False when the tab is inside a
  // group, whose head and count depend on its members: then the bar redraws.
  patch(meta, focusId = null) {
    const done = patchTab(this.list, meta, focusId, this.tabActions);
    // A patch can move .on to a tab out of reach, with no render to scroll it.
    if (done && focusId) this.focus();
    return done;
  }

  // -- renaming a group in place (#1039) -------------------------------------
  // A double click on the head, or the menu's Rename group, swaps the name for
  // an input; Enter or leaving it saves, Escape cancels (tabs.js groupTab).
  startRename(key) {
    this.renaming = key;
    this.render(this.tabs(), this.current());
  }

  saveRename(key, name) {
    this.renaming = null;
    this.act.renameGroup?.(key, name);
    this.render(this.tabs(), this.current());
  }

  cancelRename() {
    if (!this.renaming) return;
    this.renaming = null;
    this.render(this.tabs(), this.current());
  }

  // The tab in view, scrolled into the strip when it is out of reach. Only the
  // strip scrolls: scrollIntoView would also scroll a host's page (m7). Only
  // when the tab in view changed, or `force` on a resize: a redraw of any
  // other tab must not undo the person's scroll (#1094).
  focus(force = false) {
    const tab = this.list.querySelector(".tab.on");
    const id = tab?.dataset.id ?? null;
    if (!force && id === this.focused) return;
    this.focused = id;
    if (!tab || this.list.hidden) return;
    const strip = this.list.getBoundingClientRect();
    const r = tab.getBoundingClientRect();
    if (this.vertical) {
      if (r.top < strip.top) this.list.scrollTop -= strip.top - r.top;
      else if (r.bottom > strip.bottom) this.list.scrollTop += r.bottom - strip.bottom;
    } else if (r.left < strip.left) this.list.scrollLeft -= strip.left - r.left;
    else if (r.right > strip.right) this.list.scrollLeft += r.right - strip.right;
  }

  // Which header control is on, from the view the page draws.
  mark(view) {
    const on = (id, yes) => byId(this.el, id).classList.toggle("on", yes);
    on("tab-fleet", view === "fleet");
    on("menu-fleet", view === "fleet");
    on("tab-add", view === "spawn");
    on("models-btn", view === "models");
    on("settings-btn", view === "settings");
    on("journal-btn", view === "journal");
    on("knowledge-btn", view === "knowledge");
    on("usage-btn", view === "usage");
    on("files-btn", view === "files");
    on("schedules-btn", view === "schedules");
    on("help-btn", view === "help");
  }

  // The strip's button: while the tabs overflow, and always on a phone, where
  // the header has no room for another. Its count is every open session.
  size() {
    this.more.hidden = !this.vertical && !this.phone.matches && this.list.scrollWidth <= this.list.clientWidth + 1;
    this.more.replaceChildren(`${this.mount.state.ordered.length} `, icon("caret-d"));
  }

  // The connection's one line in the menu.
  setConn(state, server) {
    this.connDot.className = `dot ${state === "open" ? "ready" : state === "connecting" ? "ghost" : "err"}`;
    this.connText.textContent =
      state === "open" ? server : state === "connecting" ? this.t("header.connecting") : state === "closed" ? this.t("header.disconnected") : state;
    this.markMenu();
  }

  // The Schedules button's count, red while one is paused by errors.
  setSchedules(n, red) {
    this.schedulesN.textContent = n ? String(n) : "";
    this.schedulesN.classList.toggle("err", red);
    this.markMenu();
  }

  // The closed menu still says when something in it needs attention.
  markMenu() {
    const err = (node) => node.classList.contains("err");
    this.burger.classList.toggle("alert", err(this.connDot) || err(this.schedulesN));
  }

  setMenu(open) {
    if (open) this.root.dataset.menu = "open";
    else delete this.root.dataset.menu;
    this.burger.setAttribute("aria-expanded", String(open));
  }

  // Escape: close the phone menu and put the focus back on its button, if it
  // was open.
  close() {
    const open = this.root.dataset.menu === "open";
    this.setMenu(false);
    if (open) this.burger.focus();
    return open;
  }

  // The version and update controls the sidebar part draws: the header owns
  // this line's node, the sidebar owns what it says.
  setVersion(nodes, title = "") {
    const top = byId(this.el, "ver-top");
    top.replaceChildren(...nodes);
    top.title = title;
    top.hidden = false;
  }

  // The sessions patch while the switcher is open, and the sessions key.
  refresh() {
    this.switcher.refresh();
  }

  sessions(opts) {
    this.switcher.open(opts);
  }

  // A tab that went quiet, or came back, changes the set of tabs (#386): the
  // page redraws when a session's quiet flag differs from the one last drawn.
  quietChanged() {
    return this.mount.state.ordered.some((m) => !!m.quiet !== (this.quietSeen.get(m.key) ?? !!m.quiet));
  }

  // -- the keys --------------------------------------------------------------
  // Alt+[ and Alt+]: the Fleet, then the tabs, round.
  cycle(d) {
    const all = ["#fleet", ...this.tabs().map((m) => this.act.hashOf?.(m.key))];
    const i = all.indexOf(location.hash || "#fleet");
    this.act.go?.(all[i < 0 ? (d > 0 ? 0 : all.length - 1) : (i + d + all.length) % all.length]);
  }

  // Alt+Shift+[ and Alt+Shift+]: the tab in front one place over (#661).
  stepTab(d) {
    const id = this.current();
    const step = id && stepMove(this.list, id, d);
    if (step) this.tabActions.onMove(...step);
  }

  destroy() {
    this.headerObserver.disconnect();
    this.listObserver.disconnect();
    this.mutationObserver.disconnect();
    this.phone.removeEventListener("change", this.onPhone);
    window.removeEventListener("hashchange", this.onHideMenu);
    cancelTabDrag(this.list);
    this.tabCard.hide();
    this.switcher.close();
    this.list.remove();
    this.el.remove();
  }
}
