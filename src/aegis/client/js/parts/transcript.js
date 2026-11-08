// The transcript part: one session's rows, the navigator, find, the six
// status rows and the viewer, all built inside the element the mount gives it.
//
// It owns the markup it needs (the slice index.html used to hold), looks every
// node up under that element and never asks the page for an id. The Transcript
// class still does the hard part: the window of mounted rows, the fold levels,
// the unread marks. The part supplies it with its nodes, its live calls and
// its files' prefix, which is the mount's URL prefix rather than the page's.
//
// The transcript's files come from this mount's server: <prefix>/files/... on
// the host's own server, <prefix>/via/<server>/files/... on a linked one. The
// part sets that base before a row is drawn (a kept tab's rows mount at once)
// and hands it to render(), the viewer and every artifact frame.

import { serverNow } from "../clock.js";
import { installCopy } from "../copy.js";
import { loadTranscript, saveTranscript } from "../devicestore.js";
import { byId } from "../dom.js";
import { Find } from "../find.js";
import { icon, glyph } from "../glyphs.js";
import { forKey, keyOf } from "../mount.js";
import { Transcript } from "../transcript.js";
import { Viewer } from "../viewer.js";

const SAVE_MS = 2000;

// The markup this part owns. The ids stay as they are: they are the browser
// suite's hooks, and two mounts' copies are harmless because every lookup is
// scoped to the part's own element.
export const TRANSCRIPT_HTML = `
<div class="tr" id="tr" tabindex="-1"><p class="notice tr-offline" id="tr-offline" hidden>Not saved on this device. It opens when the connection is back.</p><div id="entries"></div><div class="row queued" id="queued" hidden><span class="t"></span><span class="g" id="queued-g"></span><div class="body"><span class="lbl" id="queued-lbl"></span><div class="text"></div><div class="ctl"><button class="btn primary" type="button" data-act="send">Send anyway</button><button class="btn" type="button" data-act="copy">Copy</button><button class="btn" type="button" data-act="edit">Edit</button><button class="btn" type="button" data-act="discard">Discard</button></div></div></div><div class="row replies" id="replies" hidden><span class="t"></span><span class="g" id="replies-g"></span><div class="body"><div id="replies-pills"></div><p class="cnote" id="handoff-done" hidden></p></div></div><div class="row coldnote" id="coldnote" hidden><span class="t"></span><span class="g" id="coldnote-g"></span><div class="body"><div class="cold"><span class="lbl">cache expired</span><span id="coldnote-t"></span><button class="btn needs-net" id="coldnote-go" type="button">Continue in a fresh session</button></div></div></div><div class="row closing" id="closing" hidden><span class="t"></span><span class="g" id="closing-g"></span><div class="body"><button class="btn archive needs-net" id="closing-btn" type="button" title="Close this session; it stays in the archive">Archive</button></div></div><div class="row working" id="working" hidden><span class="t"></span><span class="g" id="working-g"></span><div class="body"><span class="wverb"><span id="working-verb">Working…</span><span class="wcur"></span></span><span class="meta">(<span id="working-s">0</span>s · ↑ <span class="wtok" id="working-in">0</span> · ↓ <span class="wtok" id="working-out">0</span> tokens<span class="wesc"> · Esc to interrupt</span>)</span></div></div><div class="row working" id="waking" hidden><span class="t"></span><span class="g" id="waking-g"></span><div class="body">Waking <span class="meta">· resuming the harness</span></div></div></div>
<div class="nav" id="nav" hidden>
<button class="spark" id="nav-recap" title="Recap where this session stands" aria-label="Recap where this session stands"></button>
<button class="fold" id="nav-fold" data-level="0" title="Fold tool calls and thinking (Z)" aria-label="Fold tool calls and thinking"></button>
<button class="needs" id="nav-needs" title="Next session that needs you (Alt+J)" aria-label="Next session that needs you"><span class="n" id="nav-needs-n"></span></button>
<button class="up" id="nav-up" title="Previous agent message (Alt+↑)" aria-label="Previous agent message"></button>
<button class="pos" id="nav-pos" title="First unread (Alt+U)"></button>
<button class="down" id="nav-down" title="Next agent message (Alt+↓)" aria-label="Next agent message"></button>
<button class="last" id="nav-last" title="Start of the last agent message" aria-label="Start of the last agent message"></button>
<button class="latest" id="jump" title="Latest (G)" aria-label="Latest"></button>
<button class="side-btn" id="nav-side" title="Session panel (Alt+B)" aria-label="Session panel"></button>
</div>
<div class="find" id="find" hidden>
<input id="find-q" spellcheck="false" autocomplete="off" placeholder="Find in transcript" aria-label="Find in this transcript">
<span class="count" id="find-n" aria-live="polite"></span>
<button class="prev" id="find-prev" title="Previous match (Shift+Enter)" aria-label="Previous match"></button>
<button class="next" id="find-next" title="Next match (Enter)" aria-label="Next match"></button>
<button class="close" id="find-close" title="Close (Esc)" aria-label="Close"></button>
</div>
<div class="viewer" id="viewer" hidden></div>`;

const FOLDS = ["Everything shown", "Tool calls and thinking folded", "Only messages shown"];
const VERBS = ["Working", "Thinking", "Weaving", "Brewing", "Forging", "Pondering", "Tinkering", "Conjuring", "Untangling", "Crafting"];
const stillMotion = matchMedia("(prefers-reduced-motion: reduce)");

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

const fmtTokens = (n) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

export class TranscriptPart {
  constructor(mount, host) {
    this.mount = mount;
    this.key = null;
    this.base = "";
    this.unsub = null;
    this.gen = 0;
    this.placed = false;
    this.saveTimer = null;
    this.startedAt = null;
    this.workingTokens = [0, 0];
    this.workingShown = [0, 0];
    this.typer = { verb: 0, at: 0, phase: "type", wait: 0 };
    this.navFrame = 0;
    this.navDrawn = {};
    this.fold = 0;

    this.pane = el("section", "tr-pane col");
    this.pane.dataset.pane = "transcript";
    this.pane.innerHTML = TRANSCRIPT_HTML;
    host.append(this.pane);

    const n = (id) => byId(this.pane, id);
    this.tr = n("tr");
    this.entries = n("entries");
    this.jump = n("jump");
    this.offline = n("tr-offline");
    this.queued = n("queued");
    this.replies = n("replies");
    this.cold = n("coldnote");
    this.closing = n("closing");
    this.working = n("working");
    this.waking = n("waking");
    this.nav = n("nav");
    this.navFold = n("nav-fold");
    this.findBar = n("find");
    this.viewerRoot = n("viewer");

    // One viewer for this part: its Open buttons and its file prefix belong to
    // the mount, not to a page-wide default.
    this.viewer = new Viewer(this.viewerRoot, {
      entry: (id) => this.transcript.entries.get(id),
      point: null,
      base: this.base,
    });
    this.transcript = new Transcript(this.tr, this.entries, this.jump, {
      base: this.base,
      open: (item) => this.viewer.open(item),
      onSelect: () => this.drawNav(),
      onRead: (ids) => this.read(ids),
      loadDetail: (ids) => this.detail(ids),
      statPaths: (entryId) => this.stat(entryId),
    });
    this.find = new Find(this.findBar, this.transcript, (q) =>
      this.key ? this.mount.callFor(this.key, "transcript.search", { q }).then((r) => r.ids) : Promise.resolve([]),
    );

    this.copyDeps = {
      entry: (id) => this.transcript.entries.get(id),
      output: (id) => this.mount.callFor(this.key, "transcript.output", { id }).then((r) => r.text),
      detail: (id) => this.mount.callFor(this.key, "transcript.detail", { ids: [id] }).then((got) => got[0]),
    };
    installCopy(this.entries, this.copyDeps);
    installCopy(this.viewerRoot, this.copyDeps);

    this.icons();
    this.wire();
    this.drawNavNow();
    this.drawOfflineNotice();
  }

  // A part draws its own glyphs; the sprite is in the mount's root (glyphs.js
  // installed it there).
  icons() {
    const add = (id, iconName, where = "append", attention = false) => {
      const node = byId(this.pane, id);
      if (!node) return;
      const mark = attention ? glyph(iconName) : icon(iconName);
      if (where === "prepend") node.prepend(mark);
      else node.append(mark);
    };
    add("find-prev", "up");
    add("find-next", "down");
    add("find-close", "close");
    add("nav-recap", "sparkle", "prepend");
    add("nav-needs", "needs_you", "prepend", true);
    add("nav-last", "start");
    add("nav-up", "up");
    add("nav-down", "down");
    add("jump", "latest");
    add("replies-g", "prompt");
    add("queued-g", "prompt");
    add("working-g", "think");
    add("waking-g", "think");
    add("coldnote-g", "wait");
    add("closing-g", "done", "append", true);
    add("nav-side", "panel");
    this.navFold.prepend(icon("fold"));
    // A chat has no session panel and no other session to jump to.
    byId(this.pane, "nav-side").hidden = true;
    byId(this.pane, "nav-needs").hidden = true;
  }

  wire() {
    byId(this.pane, "nav-up").addEventListener("click", () => this.transcript.message(-1));
    byId(this.pane, "nav-down").addEventListener("click", () => this.transcript.message(1));
    byId(this.pane, "nav-pos").addEventListener("click", () => this.transcript.firstUnread());
    byId(this.pane, "nav-last").addEventListener("click", () => this.transcript.lastMessage());
    byId(this.pane, "nav-recap").addEventListener("click", () => this.recap());
    this.navFold.addEventListener("click", () => this.setFold((this.transcript.foldLevel + 1) % 3));
    byId(this.pane, "coldnote-go").addEventListener("click", () => this.handOff());
    byId(this.pane, "closing-btn").addEventListener("click", () => this.archive());
    this.setFold(0);
    this.workingTimer = setInterval(() => this.tickWorking(), 60);
  }

  setFold(level) {
    this.transcript.setFoldLevel(level);
    this.navFold.dataset.level = String(level);
    this.navFold.title = `${FOLDS[level]}. Press for: ${FOLDS[(level + 1) % 3].toLowerCase()} (Z)`;
  }

  // -- what the mount's sessions channel reports --------------------------
  sessionChanged(key, meta) {
    if (key !== this.key) return;
    this.renderMeta(meta);
  }

  show(key) {
    key = key || null;
    if (key === this.key && this.unsub) {
      this.renderMeta();
      return;
    }
    this.gen++;
    this.unsub?.();
    this.unsub = null;
    clearTimeout(this.saveTimer);
    this.saveTimer = null;
    if (this.key) this.mount.state.kept.set(this.key, this.transcript.stash());
    this.transcript.clear();
    this.find.close(false);
    this.key = key;
    this.placed = false;
    const where = key ? forKey(key) : { server: null, log_id: null };
    const prefix = this.mount.urls.client.pathname.replace(/\/$/, "");
    this.base = key ? prefix + (where.server ? `/via/${where.server}` : "") : prefix;
    this.transcript.base = this.base;
    this.viewer.base = this.base;
    this.viewer.show(key);
    this.drawNavNow();
    this.renderMeta();
    this.drawOfflineNotice();
    if (!key) return;
    const saved = this.mount.state.kept.get(key);
    if (saved) {
      this.mount.state.kept.delete(key);
      this.transcript.restore(saved);
      this.subscribe();
      this.drawOfflineNotice();
      this.renderMeta();
      return;
    }
    const gen = this.gen;
    loadTranscript(key).then((copy) => {
      if (gen !== this.gen) return;
      // What this device kept, drawn at once; the subscription asks only for
      // what changed since its revision.
      if (copy) this.transcript.snapshot({ entries: copy.entries, rev: copy.rev });
      this.drawOfflineNotice();
      this.renderMeta();
      this.subscribe();
    });
  }

  subscribe() {
    const { server, log_id } = forKey(this.key);
    const gen = this.gen;
    this.unsub = this.mount.conn.subscribe(
      `transcript:${log_id}`,
      (data) => {
        if (gen !== this.gen) return;
        if (data.since !== undefined) {
          this.transcript.resume(data);
          for (const entry of data.entries || []) this.mount.emit("entry", { session: this.key, entry });
        } else {
          this.transcript.snapshot(data);
          for (const entry of data.entries || []) this.mount.emit("entry", { session: this.key, entry });
          if (!this.placed) this.transcript.setSince(this.sinceText());
          this.placed = true;
        }
        this.keep();
        this.drawOfflineNotice();
        this.drawNav();
      },
      (ops) => {
        if (gen !== this.gen) return;
        this.transcript.apply(ops);
        for (const op of ops) if (op.upsert) this.mount.emit("entry", { session: this.key, entry: op.upsert });
        this.keep();
        this.drawOfflineNotice();
        this.drawNav();
      },
      (error) => {
        if (gen === this.gen) this.mount.emit("error", { session: this.key, error });
      },
      () => (this.transcript.rev >= 0 ? this.transcript.rev : null),
      server,
    );
  }

  // The divider's words: how long since this session was last read.
  sinceText() {
    const t = this.mount.state.sessions.get(this.key)?.last_read_at;
    if (!t) return "new since you left";
    const m = Math.round((serverNow() - t) / 60);
    return `new since you left · ${m < 60 ? `${m} min` : `${Math.round(m / 60)} h`}`;
  }

  // The device keeps what it has seen, as app.js does, but one part's copy.
  keep() {
    if (this.saveTimer || !this.mount.conn?.open || !this.key) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      if (!this.key || this.mount.state.sessions.get(this.key)?.state === "working") return;
      if (this.transcript.rev >= 0)
        saveTranscript(this.key, { entries: [...this.transcript.entries.values()], rev: this.transcript.rev });
    }, SAVE_MS);
  }

  read(ids) {
    const key = this.key;
    if (!key) return;
    for (let i = 0; i < ids.length; i += 500) {
      const batch = ids.slice(i, i + 500);
      this.mount.callFor(key, "session.read", { ids: batch }).catch(() => batch.forEach((id) => this.transcript.sent.delete(id)));
    }
  }

  detail(ids) {
    const key = this.key;
    return this.mount
      .callFor(key, "transcript.detail", { ids })
      .then((got) => (this.key === key ? got : []))
      .catch((error) => {
        if (this.key === key) this.mount.emit("error", { session: key, error });
        throw error;
      });
  }

  stat(entryId) {
    const key = this.key;
    return this.mount.callFor(key, "path.stat", { entry_id: entryId }).then((got) => {
      if (this.key !== key) throw new Error("another session is shown");
      return got;
    });
  }

  // -- the status rows ------------------------------------------------------
  renderMeta(meta = this.key ? this.mount.state.sessions.get(this.key) : null) {
    const s = meta && meta.key === this.key ? meta : this.key ? this.mount.state.sessions.get(this.key) : null;
    const working = s?.state === "working";
    this.working.hidden = !working;
    if (working && this.startedAt == null) {
      this.startedAt = Date.now();
      this.workingShown = [0, 0];
    }
    if (!working) this.startedAt = null;
    this.workingTokens = s?.turn_tokens || [0, 0];
    this.waking.hidden = true; // no composer warms a session in this slice
    this.drawReplies(s);
    this.drawCold(s);
    this.drawClosing(s);
    this.drawQueued(s);
    this.drawNav();
  }

  // The working row's clock, token counters and verbs, as app.js draws them.
  tickWorking() {
    if (this.startedAt == null) return;
    const n = (id) => byId(this.pane, id);
    n("working-s").textContent = Math.round((Date.now() - this.startedAt) / 1000);
    ["working-in", "working-out"].forEach((id, k) => {
      const gap = this.workingTokens[k] - this.workingShown[k];
      if (gap > this.workingShown[k] * 0.02 + 1 && !n(id).classList.contains("flash")) {
        n(id).classList.add("flash");
        setTimeout(() => n(id).classList.remove("flash"), 350);
      }
      this.workingShown[k] = Math.abs(gap) < 1 ? this.workingTokens[k] : this.workingShown[k] + gap * 0.15;
      n(id).textContent = fmtTokens(this.workingShown[k]);
    });
    const verb = n("working-verb");
    if (stillMotion.matches) {
      if (verb.textContent !== "Working…") verb.textContent = "Working…";
      return;
    }
    const word = `${VERBS[this.typer.verb]}…`;
    if (this.typer.wait > 0) this.typer.wait -= 1;
    else if (this.typer.phase === "type") {
      this.typer.at += 1;
      if (this.typer.at >= word.length) Object.assign(this.typer, { phase: "erase", wait: 50 });
    } else if (this.typer.at > 0) this.typer.at -= 1;
    else Object.assign(this.typer, { phase: "type", verb: (this.typer.verb + 1) % VERBS.length, wait: 4 });
    const text = word.slice(0, this.typer.at);
    if (verb.textContent !== text) {
      verb.replaceChildren(
        ...[...text].map((c, i) => {
          const ch = el("span", "", c);
          ch.style.animationDelay = `${i * 0.07}s`;
          return ch;
        }),
      );
    }
  }

  drawQueued(s) {
    // a6 owns the outbox; this slice builds the row and leaves it empty.
    if (!this.queued.hidden) this.queued.hidden = true;
  }

  drawReplies(s) {
    const working = s?.state === "working";
    const replies = working ? [] : s?.replies || [];
    const brief = !working && !s?.continued_by ? s?.handoff || null : null;
    const key = JSON.stringify([s?.log_id, replies, brief, s?.continued_by]);
    if (this.replies.dataset.key === key) return;
    this.replies.dataset.key = key;
    this.replies.hidden = !replies.length && !brief && !s?.continued_by;
    const keepFocus = (e) => e.preventDefault();
    const chips = replies.map((text) => {
      const b = el("button", "rp", text);
      b.addEventListener("mousedown", keepFocus);
      b.addEventListener("click", () => this.send(text));
      return b;
    });
    if (brief) {
      const h = el("button", "rp hand", `continue fresh · ${fmtTokens(s.context_tokens)}`);
      h.addEventListener("mousedown", keepFocus);
      h.addEventListener("click", () => this.handOff());
      chips.unshift(h);
    }
    byId(this.pane, "replies-pills").replaceChildren(...chips);
    const done = byId(this.pane, "handoff-done");
    done.hidden = !s?.continued_by;
    done.textContent = !s?.continued_by
      ? ""
      : s.archived
        ? `continued in ${this.handleOf(s.continued_by)}`
        : `continued in ${this.handleOf(s.continued_by)}; this tab takes no messages and is archived when it finishes its first turn`;
    if (this.transcript.following) this.transcript.toBottom();
  }

  handleOf(key) {
    if (!key) return "a fresh session";
    return this.mount.state.sessions.get(keyOf(forKey(this.key).server, key))?.handle || key;
  }

  drawCold(s) {
    const box = this.cold;
    const c = s?.cache_cold;
    const on = !!c && s.state !== "working" && !s.continued_by && (s.context_tokens ?? 0) >= c.min && serverNow() - (s.last_activity || serverNow()) >= c.after;
    const key = on ? `${s.log_id}:${s.context_tokens}` : "";
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    box.hidden = !on;
    byId(this.pane, "coldnote-go").hidden = !!s?.worker;
    byId(this.pane, "coldnote-t").textContent = on
      ? `The prompt cache has expired, so your next message rewrites all ${fmtTokens(s.context_tokens)} tokens of this context, about 20 times what a warm turn reads.`
      : "";
    if (on && this.transcript.following) this.transcript.toBottom();
  }

  drawClosing(s) {
    const box = this.closing;
    const hide = !s || s.attention !== "done";
    if (box.hidden === hide) return;
    box.hidden = hide;
    if (this.transcript.following) this.transcript.toBottom();
  }

  drawOfflineNotice() {
    this.offline.hidden = !!this.mount.conn?.open || !this.key || this.transcript.entries.size > 0;
  }

  // -- the navigator --------------------------------------------------------
  drawNav() {
    if (!this.navFrame) this.navFrame = requestAnimationFrame(() => this.drawNavNow());
  }

  drawNavNow() {
    this.navFrame = 0;
    const { index, total, unread } = this.transcript.position();
    const now = {
      hidden: !this.transcript.entries.size,
      text: total ? `${index}/${total}` : "",
      label: total ? `${unread ? `${unread} unread · ` : ""}message ${index} of ${total}` : "",
      unread: unread > 0,
      off: !total,
    };
    if (now.hidden !== this.navDrawn.hidden) this.nav.hidden = now.hidden;
    const pos = byId(this.pane, "nav-pos");
    if (now.text !== this.navDrawn.text) pos.textContent = now.text;
    if (now.label !== this.navDrawn.label) {
      pos.setAttribute("aria-label", now.label);
      pos.title = `${now.label}. First unread (Alt+U)`;
    }
    if (now.unread !== this.navDrawn.unread) pos.classList.toggle("unread", now.unread);
    if (now.off !== this.navDrawn.off)
      for (const id of ["nav-up", "nav-down", "nav-last"]) byId(this.pane, id).disabled = now.off;
    this.navDrawn = now;
  }

  // -- the calls a row makes -------------------------------------------------
  send(text) {
    return this.mount.callFor(this.key, "session.send", { text }).catch((error) => {
      this.mount.emit("error", { session: this.key, error });
    });
  }

  async handOff() {
    const key = this.key;
    try {
      const r = await this.mount.callFor(key, "session.handoff");
      const next = keyOf(forKey(key).server, r.log_id);
      this.mount.follow(next);
    } catch (error) {
      this.mount.emit("error", { session: key, error });
    }
  }

  async archive() {
    const key = this.key;
    try {
      await this.mount.callFor(key, "session.close");
    } catch (error) {
      this.mount.emit("error", { session: key, error });
    }
  }

  recap() {
    this.mount.callFor(this.key, "recap.request", { force: true }).catch((error) => {
      this.mount.emit("error", { session: this.key, error });
    });
  }

  destroy() {
    this.gen++;
    this.unsub?.();
    this.unsub = null;
    clearTimeout(this.saveTimer);
    clearInterval(this.workingTimer);
    if (this.navFrame) cancelAnimationFrame(this.navFrame);
  }
}
