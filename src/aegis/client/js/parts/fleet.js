// The Fleet: the install offer, the band over the cards and its quota, the
// order and kind segments, the cards, the linked servers' bands and cards, and
// the archive with its search, server filter, worker chip and closed terminals.
//
// The part builds the markup index.html used to hold, finds every node under
// the element it built and never asks the page for an id. It owns what the
// Fleet remembers in this browser (the order, the kinds, the unfolded groups,
// the archive's page and filter, the selection) and the page hands it what only
// the router and the network know: how to open a session, close one, read an
// archived one, where a view goes, how old the device's copy is, how a server's
// speed is measured, and the card menu. The router calls render() when the view
// shows and patch(ids) when sessions change; the keys call move(), openSel()
// and filter().

import { serverNow } from "../clock.js";
import { byId } from "../dom.js";
import { applyLabels } from "../labels.js";
import {
  ago,
  patchCard,
  renderArchive,
  renderBand,
  renderBandQuota,
  renderCards,
  tickPlan,
} from "../fleet.js";
import { gorgoneion } from "../glyphs.js";
import { startInstall } from "../install.js";
import { markNeedsNet } from "../net.js";

// The markup this part owns. The ids stay as they are: they are the browser
// suite's hooks, and two mounts' copies are harmless because every lookup is
// scoped to the part's own element.
const FLEET_HTML = `
<section class="install-offer" id="install-offer" hidden><div class="t"><span data-t="fleet.installOffer">Install aegis as an app</span><span data-t="fleet.installWhy">Opens from the home screen, works without a connection, and can notify you.</span></div><button class="btn" type="button" data-act="later" data-t="fleet.notNow">Not now</button><button class="btn primary" type="button" data-act="install" data-t="fleet.install">Install</button></section>
<section class="band" id="band">
  <div class="band-mark" id="band-mark"></div>
  <div><h5><span class="band-server" id="band-server" data-t="fleet.server">server</span><span class="band-link"></span></h5><div class="counts band-counts" id="band-counts"></div></div>
  <div><h5 data-t="sidebar.host">Host</h5><div class="band-host" id="band-host"></div></div>
  <div class="q band-quota-col" id="band-quota-col" hidden><h5><span class="qlink" id="band-quota-link" data-t-title="sidebar.quotaTitle"><span data-t="sidebar.quota">Quota</span></span><span class="age band-quota-age" id="band-quota-age"></span></h5><div class="band-quota" id="band-quota"></div></div>
</section>
<div class="order"><h3 data-t="fleet.sessions">Sessions</h3>
  <div class="seg" id="fleet-order">
    <button data-order="attention" data-t="fleet.needsFirst">Needs you first</button>
    <button data-order="tabs" data-t="fleet.tabOrder">Tab order</button>
  </div>
  <div class="seg kinds" role="radiogroup" data-t-aria="fleet.show">
    <button type="button" role="radio" data-kind="all" data-t="fleet.all">All</button>
    <button type="button" role="radio" data-kind="agents" data-t="fleet.agents">Agents</button>
    <button type="button" role="radio" data-kind="terminals" data-t="fleet.terminals">Terminals</button>
  </div>
</div>
<section class="cards" id="cards" tabindex="-1"></section>
<div id="remotes"></div>
<section class="archive">
  <div class="archive-head"><h3 data-t="fleet.archive">Archive</h3><input id="arch-q" data-t-placeholder="fleet.filter" spellcheck="false"><div class="arch-servers" id="arch-servers" hidden></div><button class="arch-workers" id="arch-workers" type="button" aria-pressed="false" data-t-title="fleet.workersTitle" hidden><span data-t="fleet.workers">Queue workers</span><b></b></button></div>
  <div id="arch-list"></div>
  <section id="arch-terms" class="arch-terms" hidden><h4 data-t="fleet.closedTerminals">Closed terminals</h4><div class="arch-term-list"></div></section>
  <div class="arch-more" id="arch-more" hidden><span id="arch-count"></span><button class="btn" id="arch-next" type="button" data-t="fleet.showMore">Show 50 more</button></div>
</section>`;

// Terminals and browsers are tools, not agents: the "terminals" kind shows both.
const isTool = (m) => m.kind === "term" || m.kind === "browser";

// A row's cell, in the closed terminals' table.
const cell = (text) => Object.assign(document.createElement("td"), { textContent: text ?? "" });

export class FleetPart {
  constructor(mount, host, actions = {}) {
    this.mount = mount;
    this.t = mount.t;
    this.act = actions;
    this.root = mount.root || host;

    // This browser's choices, like the tab order.
    this.order = localStorage.getItem("aegis.fleetOrder") || "attention";
    this.unfolded = new Set(JSON.parse(localStorage.getItem("aegis.fleet-open") || "[]"));
    this.kind = localStorage.getItem("aegis.kind") || "all";
    this.stacks = {
      get here() {
        return mount.conn.server || localStorage.getItem("aegis.server") || "";
      },
      open: this.unfolded,
      onToggle: (key) => {
        if (!this.unfolded.delete(key)) this.unfolded.add(key);
        localStorage.setItem("aegis.fleet-open", JSON.stringify([...this.unfolded]));
        this.render();
      },
    };

    // The archive's page and filter, read from the server one page at a time.
    this.archived = [];
    this.cursor = null; // the next page's cursor; null: nothing more
    this.total = 0; // the first page's count of everything matching
    this.server = null; // the server filter: null for every server
    this.counts = {}; // the first page's count per server
    this.away = []; // linked servers that were down for the first page
    this.workers = false; // queue workers shown: the chip is on
    this.workerCount = 0; // the first page's count of matching queue workers
    this.loaded = false;
    this.timer = null;
    // Closed terminals on this server (term.closed), in a section of their own
    // after the archive's list.
    this.terms = new Map(); // id -> its row
    this.termsLoaded = false;
    // A card or an archive row, by log id, re-marked after every redraw.
    this.sel = null;

    this.el = document.createElement("main");
    this.el.className = "view v-fleet";
    this.el.innerHTML = FLEET_HTML;
    applyLabels(this.el, this.t);
    host.insertBefore(this.el, host.querySelector(".v-settings") || null);

    const n = (id) => byId(this.el, id);
    this.install = n("install-offer");
    this.bandBox = n("band");
    this.orderBox = n("fleet-order");
    this.kindsBox = this.el.querySelector(".kinds");
    this.cards = n("cards");
    this.remotes = n("remotes");
    this.archQ = n("arch-q");
    this.archServers = n("arch-servers");
    this.archWorkers = n("arch-workers");
    this.archList = n("arch-list");
    this.archTerms = n("arch-terms");
    this.archTermList = this.el.querySelector(".arch-term-list");
    this.archMore = n("arch-more");
    this.archCount = n("arch-count");
    this.archNext = n("arch-next");

    n("band-mark").append(gorgoneion());
    n("band-quota-link").addEventListener("click", () => this.act.go?.("#usage"));
    startInstall(this.install);

    // The card menu the page draws for a card, a tab or a waiting row.
    this.act.attachCardMenu?.(this.cards);
    // An archive row's menu: its own Read and Reopen buttons.
    this.act.attachMenu?.(
      this.archList,
      (t) => {
        const read = t.querySelector?.("button.read");
        const again = t.querySelector?.("button.reopen");
        if (!t.dataset?.id || !read) return null;
        return {
          groups: [
            [
              { id: "read", label: this.t("fleet.read"), icon: "book", run: () => read.click() },
              again && { id: "reopen", label: this.t("fleet.reopen"), icon: "again", run: () => again.click() },
            ].filter(Boolean),
          ],
        };
      },
      { selector: "tr[data-id]" },
    );

    this.markOrder();
    this.markKind();
    for (const b of this.kindsBox.querySelectorAll("button"))
      b.addEventListener("click", () => {
        this.kind = b.dataset.kind;
        localStorage.setItem("aegis.kind", this.kind);
        this.markKind();
        this.render();
        this.loadArchive();
      });
    for (const b of this.orderBox.querySelectorAll("button"))
      b.addEventListener("click", () => {
        this.order = b.dataset.order;
        localStorage.setItem("aegis.fleetOrder", this.order);
        this.markOrder();
        this.render();
      });
    this.archWorkers.addEventListener("click", () => {
      this.workers = !this.workers;
      this.loadArchive();
    });
    this.archNext.addEventListener("click", () => this.loadArchive(true));
    this.archQ.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.loadArchive(), 200);
    });
  }

  // This server's sessions, and a linked server's.
  local() {
    return this.mount.state.ordered.filter((m) => !m.server);
  }

  on(server) {
    return this.mount.state.ordered.filter((m) => m.server === server);
  }

  // Which kinds the Fleet and the archive show: all, agents or terminals.
  kindShown(m) {
    return this.kind === "all" || (this.kind === "terminals") === isTool(m);
  }

  markOrder() {
    for (const b of this.orderBox.querySelectorAll("button")) b.classList.toggle("on", b.dataset.order === this.order);
  }

  markKind() {
    for (const b of this.kindsBox.querySelectorAll("button")) b.setAttribute("aria-checked", String(b.dataset.kind === this.kind));
  }

  // -- what the router draws through the part --------------------------------
  // The whole view, as the router shows it.
  render() {
    renderCards(this.cards, this.local().filter((m) => this.kindShown(m)), this.act.open, this.order, this.act.close, this.stacks, this.t);
    this.renderRemotes();
    this.mark(false);
    this.band();
    markNeedsNet(this.el);
    if (!this.loaded) this.loadArchive();
  }

  // Patches that only update sessions the page already shows: redraw the cards
  // once a frame, wherever they stand.
  patch(ids) {
    const home = [...ids].filter((id) => !this.mount.state.sessions.get(id)?.server);
    const regroup = home.some(
      (id) => !patchCard(this.cards, this.mount.state.sessions.get(id), this.act.open, this.order, this.act.close, this.t),
    );
    if (regroup) renderCards(this.cards, this.local().filter((m) => this.kindShown(m)), this.act.open, this.order, this.act.close, this.stacks, this.t);
    if (home.length < ids.size) this.renderRemotes();
    this.mark(false);
    this.band();
    markNeedsNet(this.el);
  }

  // The install offer is the part's; the router only shows the view.

  // The band over the cards: this server's sessions by attention and its host.
  band() {
    if (this.root.dataset.view !== "fleet") return;
    // Saved cards say how old they are while the socket is not open. Once it
    // opens the live snapshot is on its way, and the band is no longer offline.
    const saved = !this.mount.conn.open && this.local().some((m) => m.stale);
    renderBand(this.bandBox, {
      metas: this.local(),
      host: this.mount.state.host,
      server: this.mount.conn.server || localStorage.getItem("aegis.server") || "",
      link: saved ? this.act.seen?.() || "" : this.mount.state.links.length ? "this server" : "",
      off: saved,
      measure: () => this.act.measure?.(null),
      t: this.t,
    });
    for (const l of this.mount.state.links) {
      const box = this.remotes.querySelector(`[data-server="${CSS.escape(l.name)}"]`);
      if (!box) continue;
      const r = this.mount.state.remote.get(l.name);
      renderBand(box.querySelector(".band"), {
        metas: this.on(l.name),
        host: r?.host,
        server: l.name,
        link: this.linkLine(l),
        off: l.state !== "linked",
        measure: () => this.act.measure?.(l.name),
        t: this.t,
      });
    }
  }

  // The quota column, apart from the rest of the band: sessions patch up to
  // four times a second per working agent and quota about once a minute, and a
  // rebuilt row loses the hover tooltip that spells its reading out.
  quota() {
    if (this.root.dataset.view !== "fleet") return;
    renderBandQuota(this.bandBox, { quota: this.mount.state.quota, now: serverNow(), t: this.t });
    for (const box of this.remotes.children) {
      const r = this.mount.state.remote.get(box.dataset.server);
      if (r) renderBandQuota(box.querySelector(".band"), { ...this.farQuota(r.quota), now: serverNow(), t: this.t });
    }
  }

  // The line beside a linked server's name in its band.
  linkLine(l) {
    // A way back (links.py, Backlink) has no URL: that server links this one.
    const host = l.back ? this.t("fleet.linksHere") : l.url.replace(/^https?:\/\//, "");
    if (l.state === "linked") return this.t("fleet.linked", { host, rtt: l.rtt_ms != null ? ` · ${l.rtt_ms} ms` : "" });
    const since = new Date(l.since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (l.state === "offline") return this.t("fleet.linkOffline", { host, since });
    if (l.state === "connecting") return this.t("fleet.linkConnecting", { host });
    return this.t("fleet.linkFailed", { host, state: l.state, error: l.error });
  }

  // A linked server's quota, less the providers this server already shows for
  // the same account (each reading names its account by a hash).
  farQuota(q) {
    const mine = new Set((this.mount.state.quota.providers || []).filter((p) => p.account).map((p) => `${p.name}:${p.account}`));
    const providers = q.providers || [];
    const dup = (p) => p.account && mine.has(`${p.name}:${p.account}`);
    return { quota: { providers: providers.filter((p) => !dup(p)) }, same: providers.filter(dup), home: this.mount.conn.server };
  }

  // A block per linked server under this server's cards: its band, then its
  // cards. The band is a copy of this server's, so a theme styles both alike.
  renderRemotes() {
    const names = new Set(this.mount.state.links.map((l) => l.name));
    for (const box of [...this.remotes.children]) if (!names.has(box.dataset.server)) box.remove();
    for (const l of this.mount.state.links) {
      let box = this.remotes.querySelector(`[data-server="${CSS.escape(l.name)}"]`);
      if (!box) {
        box = document.createElement("div");
        box.className = "remote";
        box.dataset.server = l.name;
        const band = this.bandBox.cloneNode(true);
        band.removeAttribute("id");
        band.querySelector(".band-mark")?.remove();
        for (const n of band.querySelectorAll("[id]")) n.removeAttribute("id");
        band.classList.add("far");
        const cards = document.createElement("section");
        cards.className = "cards";
        this.act.attachCardMenu?.(cards);
        box.append(band, cards);
        this.remotes.append(box);
        const r = this.mount.state.remote.get(l.name);
        if (r) renderBandQuota(band, { ...this.farQuota(r.quota), now: serverNow(), t: this.t });
      }
      this.remotes.append(box); // keep the links' order
      const metas = this.on(l.name).filter((m) => this.kindShown(m));
      if (metas.length) renderCards(box.querySelector(".cards"), metas, this.act.open, this.order, this.act.close, this.stacks, this.t);
      else box.querySelector(".cards").replaceChildren();
    }
    this.band();
  }

  // Countdowns and the tick move with the clock; their unit is minutes.
  tick() {
    if (this.root.dataset.view !== "fleet") return;
    if (this.local().some((m) => m.stale)) this.band();
    for (const c of this.el.querySelectorAll(".card")) {
      const m = this.mount.state.sessions.get(c.dataset.id);
      if (m && !isTool(m)) {
        c.querySelector(".when").textContent = ago(m.last_activity, this.t);
        tickPlan(c, m);
      }
    }
  }

  // -- the archive -----------------------------------------------------------
  // The first page, or with `more` the next one after what is shown. A button
  // asks for more, never scrolling: the page grows only when a person asks.
  async loadArchive(more = false) {
    this.loaded = true;
    const q = this.archQ.value.trim();
    const params = q ? { query: q } : {};
    if (this.server) params.server = this.server;
    if (this.workers) params.workers = true;
    if (more && this.cursor) params.cursor = this.cursor;
    try {
      const r = await this.mount.call("archive.list", params);
      // Every row names its server; this server's rows are keyed by log id alone.
      const items = r.items.map((m) => this.mount.withKey(m, m.server && m.server !== this.mount.conn.server ? m.server : null));
      this.archived = more ? [...this.archived, ...items] : items;
      this.cursor = r.cursor;
      if (!more) {
        this.total = r.total;
        if (!this.server) this.counts = r.counts || {};
        this.away = r.offline || [];
        this.workerCount = r.workers || 0;
      }
    } catch (e) {
      // Offline, or failed: the next render reads it again.
      if (!more) [this.archived, this.cursor, this.total, this.loaded] = [[], null, 0, false];
    }
    if (!more) this.loadClosedTerms();
    this.drawArchive();
  }

  drawArchive() {
    renderArchive(this.archList, this.archived.filter((m) => this.kindShown(m)), {
      onReopen: (id) => this.act.reopen?.(id),
      onRead: (id) => this.act.read?.(id),
      t: this.t,
    });
    this.archMore.hidden = !this.archived.length;
    this.archCount.textContent = this.t("fleet.showing", { shown: this.archived.length, total: Math.max(this.total, this.archived.length) });
    this.archNext.hidden = !this.cursor;
    this.drawArchiveServers();
    this.drawArchiveWorkers();
    this.mark(false);
    markNeedsNet(this.el);
  }

  // A closed session's row, which its remove carried, joins the archive where
  // it sorts (archive.py's newest-first key), so a close does not read the first
  // page again, about 85 KB (#525). A search is the server's to match: it reads
  // the page again.
  archiveGained(m) {
    if (!this.loaded) return; // the next render reads the page, this row in it
    if (this.archQ.value.trim()) {
      this.loaded = false;
      return;
    }
    const row = this.mount.withKey(m, m.server !== this.mount.conn.server ? m.server : null);
    if (this.archived.some((x) => x.key === row.key)) return;
    if (m.worker) this.workerCount += 1;
    if (!m.worker || this.workers) {
      this.counts[m.server] = (this.counts[m.server] || 0) + 1;
      if (!this.server || this.server === m.server) {
        this.total += 1;
        const newer = (a, b) => a.last_activity > b.last_activity || (a.last_activity === b.last_activity && a.log_id > b.log_id);
        const at = this.archived.findIndex((x) => newer(row, x));
        // Past the pages shown, the next page brings it.
        if (at >= 0) this.archived.splice(at, 0, row);
        else if (!this.cursor) this.archived.push(row);
      }
    }
    this.drawArchive();
  }

  // The router reads an archived session's meta before opening it read-only.
  findArchived(id) {
    return this.archived.find((x) => x.key === id);
  }

  invalidateArchive() {
    this.loaded = false;
  }

  get archiveLoaded() {
    return this.loaded;
  }

  // Closed terminals on this server (term.closed), in a section of their own
  // after the archive's list. Read opens one read-only, from `terms`.
  async loadClosedTerms() {
    let rows = [];
    try {
      rows = await this.mount.call("term.closed");
      this.termsLoaded = true;
    } catch {
      // Offline, or a server without terminals: the section stays empty.
    }
    this.terms.clear();
    for (const t of rows) this.terms.set(t.id, t);
    const shownRows = this.kind === "agents" ? [] : rows;
    this.archTerms.hidden = !shownRows.length;
    const t = document.createElement("table");
    t.className = "tbl";
    for (const row of shownRows) {
      const tr = t.insertRow();
      tr.dataset.id = row.id;
      for (const [cls, text] of [
        [null, row.name],
        ["m", row.title],
        ["m", ago(row.closed_at, this.t)],
        ["m", row.code == null ? "-" : this.t("fleet.exit", { code: row.code })],
      ]) {
        const td = cell(text ?? "");
        if (cls) td.className = cls;
        tr.append(td);
      }
      const read = Object.assign(document.createElement("button"), { type: "button", className: "btn", textContent: this.t("fleet.read") });
      read.title = this.t("fleet.replay");
      read.addEventListener("click", () => this.act.go?.(`#t=${row.id}`));
      tr.insertCell().append(read);
    }
    this.archTermList.replaceChildren(t);
  }

  closedTerm(id) {
    return this.terms.get(id);
  }

  get closedTermsLoaded() {
    return this.termsLoaded;
  }

  // Queue workers are left out of the archive unless this chip is on (#331): a
  // worker's result went to whoever enqueued it. The chip says how many match,
  // and goes away when none do.
  drawArchiveWorkers() {
    const b = this.archWorkers;
    b.hidden = !this.workerCount && !this.workers;
    b.classList.toggle("on", this.workers);
    b.setAttribute("aria-pressed", String(this.workers));
    b.querySelector("b").textContent = this.workerCount;
  }

  // The archive's server filter, once a server is linked: every server with its
  // count, and a down one named so its rows are not silently missing.
  drawArchiveServers() {
    const box = this.archServers;
    box.hidden = !this.mount.state.links.length;
    if (!this.mount.state.links.length) return;
    const all = Object.values(this.counts).reduce((a, n) => a + n, 0);
    const pick = (label, server, n) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.server = server || "";
      b.className = this.server === server ? "on" : "";
      b.append(label, Object.assign(document.createElement("b"), { textContent: n }));
      b.addEventListener("click", () => {
        this.server = server;
        this.loadArchive();
      });
      return b;
    };
    const off = (name) => Object.assign(document.createElement("span"), { className: "off", textContent: this.t("fleet.offline", { name }) });
    box.replaceChildren(
      pick(this.t("fleet.all"), null, all),
      ...Object.entries(this.counts).map(([name, n]) => pick(name, name === this.mount.conn.server ? this.mount.conn.server : name, n)),
      ...this.away.map(off),
    );
  }

  // -- the selection ---------------------------------------------------------
  items() {
    return [...this.el.querySelectorAll("#cards .card, #remotes .card, #arch-list tr[data-id]")];
  }

  mark(scroll) {
    for (const n of this.el.querySelectorAll("#cards .sel, #remotes .sel, #arch-list .sel")) n.classList.remove("sel");
    const n = this.items().find((x) => x.dataset.id === this.sel);
    if (!n) {
      this.sel = null;
      return;
    }
    n.classList.add("sel");
    if (scroll) n.scrollIntoView({ block: "nearest" });
  }

  move(delta) {
    const items = this.items();
    const i = items.findIndex((x) => x.dataset.id === this.sel);
    const n = i < 0 ? items[0] : items[i + delta];
    if (!n) return;
    this.sel = n.dataset.id;
    this.mark(true);
  }

  openSel() {
    const n = this.items().find((x) => x.dataset.id === this.sel);
    if (!n) return;
    if (n.classList.contains("card")) this.act.open?.(this.sel);
    else this.act.read?.(this.sel);
  }

  // What a page-wide key focused: the node the menu opens for.
  selected() {
    return this.items().find((x) => x.dataset.id === this.sel);
  }

  focus() {
    this.cards.focus({ preventScroll: true });
    if (!this.sel) this.move(1);
  }

  filter() {
    this.archQ.focus();
  }

  destroy() {
    clearTimeout(this.timer);
    this.el.remove();
  }
}
