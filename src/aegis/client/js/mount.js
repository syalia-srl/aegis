// One mount: an element a host gives us, the .aegis root we build in it, one
// shared socket, one label table, and the parts the layout asks for.
//
// The module's own URL is the only configuration a mount needs: it was loaded
// from <prefix>/static/js/mount.js, so <prefix>/ is two steps up and every
// address (the websocket, the client's files, the sent files) is derived from
// it. A host that serves the client under /aegis gets /aegis/ws and
// /aegis/files/... without setting anything.
//
// The handle mount() returns is deliberately small (phase A; #962 adds the
// rest): on(name, fn) for connection, turnEnd, entry and error; send(text),
// which is a no-op until the composer part (a6); and destroy(), which removes
// the root and gives the socket back. A part is added by a later slice with
// its markup, its nodes and its live wiring.

import { acquire } from "./connection.js";
import { installGlyphs } from "./glyphs.js";
import { labelsFor } from "./labels.js";
import { TranscriptPart } from "./parts/transcript.js";

// Where this module came from: <prefix>/static/js/. Everything else is
// relative to it, so a mount needs no configuration to find its server.
const SELF = new URL(".", import.meta.url);
const CLIENT = new URL("../../", SELF); // <prefix>/

const LINKED = new Set(); // the stylesheet hrefs this page already holds

export function mount(el, options = {}) {
  const m = new Mount(el, options);
  m.start();
  return m.handle();
}

export class Mount {
  constructor(el, options = {}) {
    this.el = el;
    this.opts = {
      layout: "chat",
      theme: "ink",
      lang: "en",
      labels: {},
      composer: true,
      dictation: false,
      commands: false,
      attachments: true,
      session: null,
      token: "",
      ...options,
    };
    this.urls = {
      client: CLIENT,
      static: new URL("static/", CLIENT),
      ws: new URL("ws", CLIENT),
      files: (server) => new URL(server ? `via/${server}/files/` : "files/", CLIENT),
    };
    this.t = labelsFor(this.opts.lang, this.opts.labels);
    this.listeners = new Map(); // event name -> Set of handlers
    this.state = new MountState();
    this.parts = [];
    this.root = null;
    this.conn = null;
    this._release = null;
    this._unsubSessions = null;
    this._working = new Set(); // keys whose turn this mount saw start
    this._costAt = new Map(); // key -> the cumulative cost_usd when it did
    this._dead = false;
  }

  start() {
    this.root = makeRoot(this.el, this.opts, this.urls);
    const held = acquire(this.urls.ws.href, {
      token: this.opts.token,
      onState: (state, server) => this.emit("connection", { state, server }),
    });
    this.conn = held.conn;
    this._release = held.release;
    this._unsubSessions = this.conn.subscribe(
      "sessions",
      (list) => this._snapshot(list),
      (ops) => this._patch(ops),
    );
    this.mountLayout();
  }

  // Phase A composes the chat layout only. "full" is accepted (the standalone
  // page passes it at b1) but composes nothing until b8.
  mountLayout() {
    if (this.opts.layout === "full") return;
    if (this.opts.layout !== "chat") throw new Error(`aegis: no layout ${this.opts.layout}`);
    const chat = el("div", "aegis-chat");
    this.root.append(chat);
    this.transcript = this.add(new TranscriptPart(this, chat));
    this.follow(this.opts.session);
  }

  add(part) {
    this.parts.push(part);
    return part;
  }

  // The host's handle.
  handle() {
    return {
      el: this.root,
      on: (name, fn) => this.on(name, fn),
      send: (text) => this.send(text),
      destroy: () => this.destroy(),
    };
  }

  on(name, fn) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(fn);
    return this;
  }

  emit(name, data) {
    for (const fn of this.listeners.get(name) || []) {
      try {
        fn(data);
      } catch (e) {
        console.error("aegis:", name, e);
      }
    }
  }

  send(text) {
    return this.composer ? this.composer.send(text) : Promise.resolve();
  }

  destroy() {
    if (this._dead) return;
    this._dead = true;
    for (const p of this.parts) p.destroy?.();
    this._unsubSessions?.();
    this._release?.();
    this.root?.remove();
    this.listeners.clear();
    this.parts = [];
  }

  // A chat has one session; the full layout picks one from its tabs.
  follow(key) {
    this.state.shown = key || null;
    if (this.transcript) this.transcript.show(this.state.shown);
  }

  call(op, params = {}, server = null) {
    return this.conn.call(op, params, server);
  }

  callFor(key, op, params = {}) {
    const { server, log_id } = forKey(key);
    return this.conn.call(op, { ...params, log_id }, server);
  }

  // `sessions`: a snapshot replaces every meta this mount holds. A session
  // already working when this mount arrived is a turn it did not see start, so
  // neither its cost nor its end is invented: it is forgotten here and a later
  // patch that leaves working reports a null cost.
  _snapshot(list) {
    this.state.sessions.clear();
    const seen = new Set();
    for (const raw of list || []) {
      if (!raw?.log_id) continue;
      const meta = { ...raw, key: raw.log_id };
      this.state.sessions.set(meta.key, meta);
      seen.add(meta.key);
      if (meta.state !== "working") this._forget(meta.key);
    }
    for (const key of [...this._working]) if (!seen.has(key)) this._forget(key);
    this.transcript?.sessionChanged(this.state.shown, this.state.sessions.get(this.state.shown));
  }

  _patch(ops) {
    for (const op of ops || []) {
      if (op.upsert) {
        this._put(op.upsert);
      } else if (op.merge) {
        const held = this.state.sessions.get(op.merge.log_id);
        if (!held) continue;
        this._put({ ...held, ...op.merge });
      } else if (op.remove !== undefined) {
        this.state.sessions.delete(op.remove);
        this._forget(op.remove);
        if (this.state.shown === op.remove) {
          this.state.shown = null;
          this.transcript?.show(null);
        } else {
          this.transcript?.sessionChanged(op.remove, undefined);
        }
      }
    }
  }

  _put(raw) {
    const key = raw.log_id;
    if (!key) return;
    const prev = this.state.sessions.get(key);
    const meta = { ...raw, key };
    this.state.sessions.set(key, meta);
    this.transcript?.sessionChanged(key, meta);
    const was = prev?.state === "working";
    const now = meta.state === "working";
    if (now && !was && prev) {
      // The patch that enters working: the cost before this turn is the
      // cumulative figure it will add to (a fresh session has none yet, which
      // is a cumulative cost of zero). A session first heard of already
      // working is a turn this mount joined halfway; it is not tracked, so its
      // end reports no cost rather than part of one.
      this._working.add(key);
      this._costAt.set(key, Number.isFinite(meta.cost_usd) ? meta.cost_usd : 0);
      return;
    }
    if (was && !now) {
      const start = this._costAt.get(key);
      const end = Number.isFinite(meta.cost_usd) ? meta.cost_usd : null;
      const seen = this._working.has(key);
      this._forget(key);
      if (!this.reportsTurn(key)) return;
      this.emit("turnEnd", {
        session: key,
        cost_usd: seen && end != null ? end - start : null,
        line: meta.attention_line || null,
        meta,
      });
    }
  }

  // Which sessions this mount reports turns for. A chat follows one session,
  // and a page may hold one chat per host tab (skene's seven), so it reports
  // only the session it shows; otherwise every mount would fire for every
  // turn on the server. The full layout shows every session the caller may
  // see and reports them all (b1). The cost tracking above stays per key, so a
  // follow() to another session mid-turn still has the start it recorded.
  reportsTurn(key) {
    return this.opts.layout === "full" || key === this.state.shown;
  }

  _forget(key) {
    this._working.delete(key);
    this._costAt.delete(key);
  }
}

export class MountState {
  constructor() {
    this.sessions = new Map(); // log_id -> meta, from the sessions channel
    this.shown = null; // the key of the session a chat shows
    this.kept = new Map(); // key -> the transcript part's stash (a4)
  }
}

// A session's key, and back. Server names never hold a slash (links.py).
export const keyOf = (server, logId) => (server ? `${server}/${logId}` : logId);

export function forKey(key) {
  const i = String(key).indexOf("/");
  return i < 0
    ? { server: null, log_id: key }
    : { server: key.slice(0, i), log_id: key.slice(i + 1) };
}

function el(tag, cls) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  return n;
}

// The root the mount owns: a .aegis element inside the host's, its theme, the
// stylesheets it needs and the glyph sprite. The host decides nothing.
function makeRoot(host, opts, urls) {
  const root = el("div", "aegis");
  root.dataset.theme = opts.theme;
  root.dataset.layout = opts.layout;
  host.append(root);
  linkStyles(opts.theme, urls);
  installGlyphs(root);
  return root;
}

// One <link> per stylesheet for the page, however many mounts ask: a chat and
// the full layout on one page share the fonts, the base rules and, when they
// picked the same theme, its file. "host" loads no aegis theme at all — the
// host's own CSS sets the variables (a3 writes the fallback block).
function linkStyles(theme, urls) {
  const sheets = ["css/fonts.css", "css/client.css"];
  if (theme !== "host" && /^[a-z][a-z0-9-]*$/.test(theme)) sheets.push(`themes/${theme}.css`);
  const have = new Set(
    [...document.querySelectorAll('link[rel~="stylesheet"]')].map((n) => n.href),
  );
  for (const name of sheets) {
    const href = new URL(name, urls.static).href;
    if (LINKED.has(href) || have.has(href)) continue;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.append(link);
    LINKED.add(href);
  }
}
