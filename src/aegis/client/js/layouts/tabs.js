// The tabs layout: the sessions the policy lists for this caller, as a strip,
// with one session's transcript and box under it and a button that starts a
// new one (spec 06, #962).
//
// The strip is a part like the transcript and the composer: it owns its markup
// and its nodes, looks them up under its own element and never asks the page
// for an id. A host that already has a #tabs keeps it: the strip is
// addressable only by class, `.aegis-tabstrip`, `.aegis-tab` and `.aegis-pane`.
//
// The sessions are the mount's own view of the sessions channel, which the
// mount already subscribes for every layout but full; the policy filtered it,
// so the layout never calls session.list itself. A session is drawn when its
// meta has no `kind`: terminals and browsers have one, agent sessions do not.
//
// The shown session changes only through mount.open() (m1), so a click, a
// spawn and the sessions channel take the same path and follow() emits
// sessionChanged once. The constructor's own session was followed quietly by
// the mount, before this layout was built, so it is never news.
//
// A redraw reuses the button it already has. The sessions channel patches
// often, and replacing every tab on each patch would drop a press that is
// between mousedown and mouseup; the strip is only rebuilt when the set or the
// order of its tabs changes, and the click is heard once on the strip itself.

import { ComposerPart } from "../parts/composer.js";
import { TranscriptPart } from "../parts/transcript.js";

export class TabsLayout {
  constructor(mount, host) {
    this.mount = mount;
    this.t = mount.t;
    this.el = document.createElement("div");
    this.el.className = "aegis-tabs";
    this.strip = document.createElement("div");
    this.strip.className = "aegis-tabstrip";
    this.pane = document.createElement("div");
    this.pane.className = "aegis-pane";
    this.el.append(this.strip, this.pane);
    host.append(this.el);
    this.tabs = new Map(); // key -> button, reused across draws
    this.empty = null; // the note a strip with no sessions shows
    this.newEl = this.newButton();
    // One listener for every tab, however often the strip is redrawn.
    this.strip.addEventListener("click", (ev) => {
      const b = ev.target.closest?.(".aegis-tab");
      if (!b || !this.strip.contains(b)) return;
      if (b === this.newEl) this.mount.spawn().catch(() => {});
      else if (b.dataset.key) this.mount.open(b.dataset.key);
    });
    mount.transcript = mount.add(new TranscriptPart(mount, this.pane));
    if (mount.opts.composer) mount.composer = mount.add(new ComposerPart(mount, this.pane));
    this.draw();
  }

  // What the strip lists: the agent sessions this mount may see, by key, so
  // two draws of the same state put the tabs in the same order.
  sessions() {
    return [...this.mount.state.sessions.values()]
      .filter((m) => m && !m.kind)
      .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  }

  // The strip, from the mount's state. Called after every sessions snapshot
  // and patch, and on every follow(), so the one shown tab stays marked. The
  // mount came up empty, or the session it showed left: the first session is
  // the one it shows, through open(), so the change is reported once.
  draw() {
    const list = this.sessions();
    const shown = this.mount.state.shown;
    const keys = new Set(list.map((m) => m.key));
    for (const [key, b] of this.tabs) {
      if (keys.has(key)) continue;
      b.remove();
      this.tabs.delete(key);
    }
    const kids = list.map((m) => this.tabFor(m, m.key === shown));
    if (!list.length) kids.push(this.emptyNote());
    kids.push(this.newEl);
    // Only touch the DOM when the set or order changed; a patch that carries
    // nothing new must not move a button the pointer is already on.
    const now = this.strip.children;
    if (kids.length !== now.length || kids.some((n, i) => now[i] !== n))
      this.strip.replaceChildren(...kids);
    if (list.length && (!shown || !list.some((m) => m.key === shown)))
      this.mount.open(list[0].key);
  }

  tabFor(m, on) {
    let b = this.tabs.get(m.key);
    if (!b) {
      b = document.createElement("button");
      b.type = "button";
      b.className = "aegis-tab";
      b.dataset.key = m.key;
      this.tabs.set(m.key, b);
    }
    b.classList.toggle("on", on);
    const name = m.title || m.handle || m.key;
    if (b.textContent !== name) b.textContent = name;
    const tip = m.title ? `${m.handle} · ${m.title}` : m.handle || m.key;
    if (b.title !== tip) b.title = tip;
    return b;
  }

  emptyNote() {
    if (!this.empty) {
      this.empty = document.createElement("span");
      this.empty.className = "aegis-tabs-empty";
      this.empty.textContent = this.t("tabs.empty");
    }
    return this.empty;
  }

  newButton() {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "aegis-tab aegis-tab-new";
    b.textContent = "+";
    b.title = this.t("tabs.newTitle");
    b.setAttribute("aria-label", b.title);
    return b;
  }

  follow() {
    this.draw();
  }

  destroy() {
    this.el.remove();
  }
}
