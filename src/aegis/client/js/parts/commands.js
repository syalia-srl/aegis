// The composer's command menu as a part: the list that opens on "/" and on
// Alt+/, built into the composer's [data-slot=menu].
//
// The menu itself (commands.js) only decides which rows match what is typed;
// this part gives it its nodes under the composer it belongs to, keeps the
// catalogs (one per session, fetched when that session's menu first opens) and
// exposes the same small surface the composer and the key table call:
// openInline, openOverlay, close, refresh, onKey, known, setLine and forget.
//
// The catalog is the owner's to fetch (`fetch(key)`), because a full layout
// asks the whole page and a chat asks its own mount. The cache is the part's,
// so two composers on one page never share a stale list, and a /model that
// changed the efforts forgets only the session it ran in.

import { CommandMenu } from "../commands.js";
import { byId } from "../dom.js";
import { applyLabels } from "../labels.js";
import { forKey } from "../mount.js";
import { toast } from "../toast.js";

export const MENU_HTML =
  '<div class="cmd-menu" id="cmd-menu" hidden><input id="cmd-filter" hidden spellcheck="false" placeholder="/command"><div id="cmd-rows"></div></div>';

// The catalog a chat's composer offers: this session's commands, the tutorial
// pages /tutorial completes and the agents /spawn completes on the session's
// own server. A chat follows one server and subscribes no links, so the
// agents of linked servers (which the full page offers) are not listed here.
export async function catalogFor(mount, key) {
  const { server } = forKey(key);
  const [cat, pages, roster] = await Promise.all([
    mount.callFor(key, "commands.list"),
    mount.conn.call("help.pages", {}, server).catch(() => []),
    mount.conn.call("agents.list", {}, server).catch(() => null),
  ]);
  const agents = (roster?.agents || [])
    .filter((a) => a.enabled)
    .sort((a, b) => (b.name === roster.default) - (a.name === roster.default))
    .map((a) => ({ v: a.name, model: a.model, doc: `${a.harness} · ${a.model}`, source: "" }));
  return { ...cat, agents, tutorials: pages.filter((p) => p.kind === "tutorial") };
}

export class CommandsPart {
  // host: the composer element that holds [data-slot=menu].
  // fetch(key): Promise<catalog> for a session; run(line): send a line the
  // overlay typed; getLine/setLine: the composer's box; meta(): its session.
  constructor(mount, host, opts = {}) {
    this.mount = mount;
    this.t = mount.t;
    this.opts = opts;
    this.catalogs = new Map(); // key -> the catalog promise, so typing waits on one call
    const slot = host.querySelector('[data-slot="menu"]');
    if (!slot) throw new Error("aegis: the composer has no menu slot");
    slot.innerHTML = MENU_HTML;
    applyLabels(slot, this.t);
    // The menu box is the slot's child; the slot stays in the composer's flow
    // as an empty, zero-height div while the box is absolutely placed.
    this.el = slot.firstElementChild;
    this.menu = new CommandMenu({
      box: this.el,
      rows: byId(this.el, "cmd-rows"),
      filter: byId(this.el, "cmd-filter"),
      load: () => this.load(),
      run: (line) => opts.run(line),
      getLine: () => opts.getLine(),
      setLine: (v) => opts.setLine(v),
      meta: () => opts.meta(),
      t: this.t,
    });
  }

  get isOpen() {
    return this.menu.isOpen;
  }

  openInline() {
    return this.menu.openInline();
  }

  openOverlay(prefix) {
    return this.menu.openOverlay(prefix);
  }

  close() {
    this.menu.close();
  }

  refresh() {
    this.menu.refresh();
  }

  onKey(ev) {
    return this.menu.onKey(ev);
  }

  known(line) {
    return this.menu.known(line);
  }

  // The composer's line, as a command inserts it (the menu's own setText, so an
  // overlay writes its filter and an inline menu the box).
  setLine(v) {
    this.menu.setText(v);
  }

  // A session's catalog changed under it (/model changes the efforts).
  forget(key) {
    this.catalogs.delete(key);
  }

  // The session's catalog, once per session until it is forgotten. A failure
  // says so and leaves the menu as it is: the next "/" asks again.
  async load() {
    const key = this.opts.meta()?.key;
    if (!key || !this.opts.fetch) return null;
    if (!this.catalogs.has(key)) this.catalogs.set(key, this.opts.fetch(key));
    try {
      return await this.catalogs.get(key);
    } catch (e) {
      this.catalogs.delete(key);
      toast(e.message, { kind: "error", root: this.mount.root });
      return null;
    }
  }

  destroy() {
    this.close();
  }
}
