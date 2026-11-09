// One mount: an element a host gives us, the .aegis root we build in it, one
// shared socket, one label table, and the parts the layout asks for.
//
// The module's own URL is the only configuration a mount needs: it was loaded
// from <prefix>/static/js/mount.js, so <prefix>/ is two steps up and every
// address (the websocket, the client's files, the sent files) is derived from
// it. A host that serves the client under /aegis gets /aegis/ws and
// /aegis/files/... without setting anything.
//
// The handle mount() returns is deliberately small: on(name, fn) for
// connection, sessionChanged, turnStart, turnEnd, entry, fileSent and error;
// open(session), spawn(agent, prompt) and focus() for a host that moves the
// mount itself; send(text), which the composer part sends (a6); and destroy(),
// which removes the root and gives the socket back. A part is added by a later
// slice with its markup, its nodes and its live wiring.
//
// The outbox is per mount (a6) even when no composer draws it: drafts are one
// device's, keyed by session, and a chat that is only a transcript still puts
// one out when the connection comes back. A page that already has one lends it
// instead of letting this mount open a second window on the same drafts.

import { acquire } from "./connection.js";
import { installGlyphs } from "./glyphs.js";
import { labelsFor } from "./labels.js";
import { lifecycle } from "./lifecycle.js";
import { Outbox } from "./outbox.js";
import { buildFull } from "./layouts/full.js";
import { ComposerPart } from "./parts/composer.js";
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
      controls: true,
      dictation: false,
      commands: false,
      attachments: true,
      // A host's own tool cards: tool name -> (entry, el) => boolean. The
      // transcript part hands them to entries.js, which calls one when the
      // call is no longer running and keeps its body only when it says true.
      renderers: {},
      // A host's own speech service, in place of the browser's Whistle workers.
      transcribe: null,
      session: null,
      agent: null,
      token: "",
      // `adopt`: build no root, take the element the host gives us as one. The
      // standalone page does this over its empty #a2 (b8); a host that has an
      // element of its own passes only `layout`.
      adopt: false,
      // The page's own outbox, when it already has one: the full layout's
      // module singleton (outbox.js) is what its queued row and the browser
      // suite call by name, so the mount borrows it rather than opening a
      // second window onto the same drafts.
      outbox: null,
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
    this._handle = null;
    this._spawning = null; // the spawn a send is waiting on, shared by its peers
    this.outbox = options.outbox || null;
    this.composer = null;
  }

  start() {
    this.root = makeRoot(this.el, this.opts, this.urls);
    if (!this.outbox) {
      this.outbox = new Outbox({ root: this.root, t: this.t });
      // Until a composer draws in this mount, a queued draft still goes out: the
      // composer replaces this with the fuller path (the 5 s verdict and the
      // untested-harness question) when it is built.
      this.outbox.setSender((key, params) => this.callFor(key, "session.send", params));
      this.outbox.load();
    }
    const held = acquire(this.urls.ws.href, {
      token: this.opts.token,
      onState: (state, server) => {
        this.emit("connection", { state, server });
        // A chat flushes only the session it shows: every draft on the device
        // is another mount's or the full layout's to send.
        if (state === "open" && !this.opts.outbox)
          this.outbox.flushNow((key) => key === this.state.shown).catch(() => {});
      },
    });
    this.conn = held.conn;
    this._release = held.release;
    // A chat follows one server's sessions and keeps the metas it draws. The
    // full layout already subscribes them itself (linked servers included,
    // with the Fleet and the tabs to redraw), and two writers of one map would
    // clear each other: it leaves the map to the page and b1 gives that page
    // this mount's state.
    if (this.opts.layout !== "full") {
      this._unsubSessions = this.conn.subscribe(
        "sessions",
        (list) => this._snapshot(list),
        (ops) => this._patch(ops),
      );
    }
    this.mountLayout();
  }

  // The standalone client's page: the full layout builds the netbanner, the
  // boot view and the session view in this root, and app.js composes the parts
  // it holds. It keeps the id the browser suite addresses (b8). A chat composes
  // one transcript and, unless the host asked for none, its box.
  mountLayout() {
    if (this.opts.layout === "full") {
      this.root.id = "a2";
      this.root.classList.add("app");
      buildFull(this.root, this.t);
      return;
    }
    if (this.opts.layout !== "chat") throw new Error(`aegis: no layout ${this.opts.layout}`);
    const chat = el("div", "aegis-chat");
    this.root.append(chat);
    this.transcript = this.add(new TranscriptPart(this, chat));
    if (this.opts.composer) this.composer = this.add(new ComposerPart(this, chat));
    // The constructor's session is not news to the host: it passed it.
    this.follow(this.opts.session, true);
  }

  add(part) {
    this.parts.push(part);
    return part;
  }

  // The host's handle: one small object, built once, whose calls are the
  // mount's own. It is not `this`, so a host cannot reach emit, call or a part.
  handle() {
    if (this._handle) return this._handle;
    const h = {
      el: this.root,
      on: (name, fn) => {
        this.on(name, fn);
        return h;
      },
      open: (session) => {
        this.open(session);
        return h;
      },
      spawn: (agent, prompt) => this.spawn(agent, prompt),
      send: (text) => this.send(text),
      focus: () => this.focus(),
      destroy: () => this.destroy(),
    };
    this._handle = h;
    return h;
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

  // A refused call or a channel's error reaches the host as code and message,
  // beside the OpError itself: a host that only logs must not need to know the
  // client's error class to say what went wrong (the plan's `error` event).
  fail(session, error) {
    const code = error?.code || "error";
    const text = String(error?.message ?? error ?? "");
    // An OpError's message is "code: text" (protocol.js); a host gets the text.
    const message = text.startsWith(`${code}: `) ? text.slice(code.length + 2) : text;
    this.emit("error", { session, code, message, error });
  }

  send(text) {
    if (this.composer) return this.composer.send(text);
    return this.ensureSession().then((key) => {
      // A transcript-only mount still lets a reply pill's send arrive.
      if (!key) return;
      return this.callFor(key, "session.send", { text }).catch((error) => this.fail(key, error));
    });
  }

  // Show another session (a log id or a `server/log_id` key). The constructor's
  // own session is followed quietly; this always reports when it changes.
  open(session) {
    if (this.opts.layout === "full") return this.hooks?.open?.(session) ?? this;
    this.follow(session || null);
    return this;
  }

  // Start a session through session.spawn (the policy decides), open it, and
  // send it a first prompt when one was given. A refusal is the error event
  // and a rejected promise, never a thrown string.
  async spawn(agent = this.opts.agent, prompt = null) {
    const params = {};
    if (agent) params.agent = agent;
    if (prompt) params.prompt = prompt;
    let out;
    try {
      out = await this.call("session.spawn", params);
    } catch (error) {
      this.fail(null, error);
      throw error;
    }
    this.open(out.log_id);
    return { session: out.log_id, handle: out.handle };
  }

  // The session a send writes to: the one shown, or one the host's agent starts
  // first (spec 06: an agent starts one on the first message). Two sends that
  // race before the spawn answers share the one promise: one session, never two.
  async ensureSession() {
    if (this.state.shown) return this.state.shown;
    if (!this.opts.agent) return null;
    if (!this._spawning) {
      this._spawning = this.spawn(this.opts.agent)
        .then(({ session }) => session)
        .finally(() => {
          this._spawning = null;
        });
    }
    return this._spawning;
  }

  // Put the caret in this mount's box, or focus the transcript when it has
  // none (its .tr has tabindex="-1").
  focus() {
    if (this.opts.layout === "full" && this.hooks?.focus) return this.hooks.focus();
    if (this.composer?.input) return this.composer.input.focus();
    this.transcript?.tr?.focus();
  }

  destroy() {
    if (this._dead) return;
    this._dead = true;
    for (const p of this.parts) p.destroy?.();
    if (!this.opts.outbox) this.outbox?.destroy();
    this._unsubSessions?.();
    this._release?.();
    this.root?.remove();
    this.listeners.clear();
    this.parts = [];
  }

  // A chat has one session; the full layout picks one from its tabs. The
  // constructor's session passes `quiet`, and it is the only first follow: a
  // host already knows what it passed.
  follow(key, quiet = false) {
    const previous = this.state.shown;
    key = key || null;
    this.state.shown = key;
    if (this.transcript) this.transcript.show(key);
    if (this.composer) this.composer.show(key);
    this.view?.follow?.(key);
    if (!quiet && previous !== key)
      this.emit("sessionChanged", {
        session: key,
        previous,
        meta: key ? this.state.sessions.get(key) || null : null,
      });
  }

  call(op, params = {}, server = null) {
    return this.conn.call(op, params, server);
  }

  callFor(key, op, params = {}) {
    const { server, log_id } = forKey(key);
    return this.conn.call(op, { ...params, log_id }, server);
  }

  // The key helpers, on the mount: a session's key, and back; the link that
  // holds a server; an operation on whichever server holds a session. They are
  // methods because they read this mount's links (b1 moved them off the page).
  keyOf(server, logId) {
    return keyOf(server, logId);
  }

  forKey(key) {
    return forKey(key);
  }

  linkOf(server) {
    return this.state.links.find((l) => l.name === server);
  }

  isOff(server) {
    return !!server && this.linkOf(server)?.state !== "linked";
  }

  // A session's meta as the page's views draw it: its lifecycle, its server,
  // its key, and whether that server is unreachable.
  withKey(m, server = null) {
    return { ...lifecycle(m), server, key: keyOf(server, m.log_id), off: this.isOff(server) };
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
    const meta = this.state.sessions.get(this.state.shown);
    this.transcript?.sessionChanged(this.state.shown, meta);
    this.composer?.sessionChanged(this.state.shown, meta);
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
          // One place for the shown session changing: follow() reports it.
          this.follow(null);
        } else {
          this.transcript?.sessionChanged(op.remove, undefined);
          this.composer?.sessionChanged(op.remove, undefined);
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
    this.composer?.sessionChanged(key, meta);
    this.noteTurn(key, prev, meta);
  }

  // The working transition of one session's row, for every layout: turnStart
  // when it enters working in a patch this mount saw, turnEnd when it leaves.
  // The cost before the turn is the cumulative figure it will add to (a fresh
  // session has none yet, which is a cumulative cost of zero). A session first
  // heard of already working is a turn this mount joined halfway; it is not
  // tracked, so its end reports no cost rather than part of one.
  noteTurn(key, prev, meta) {
    const was = prev?.state === "working";
    const now = meta.state === "working";
    if (now && !was && prev) {
      this._working.add(key);
      this._costAt.set(key, Number.isFinite(meta.cost_usd) ? meta.cost_usd : 0);
      if (this.reportsTurn(key)) this.emit("turnStart", { session: key, meta });
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
    this.sessions = new Map(); // key -> meta, from the sessions channel
    this.links = []; // the home server's links, from the `links` channel
    this.linksLoaded = false; // the first `links` snapshot has arrived
    this.linksReady = new Promise((ok) => (this.linksArrived = ok)); // resolves with it
    this.linksSig = ""; // the links' names and states the archive was last read for
    this.remote = new Map(); // linked server -> {unsubs, quota, host, unsubHost, loaded, state}
    this.quota = { providers: [] };
    this.host = null;
    this.unsubHost = null;
    this.ordered = []; // metas in this browser's tab order
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
// stylesheets it needs and the glyph sprite. The host decides nothing. With
// `adopt`, the host's element is the root: the standalone page gives it an
// empty #a2, and the mount fills it with the full layout.
function makeRoot(host, opts, urls) {
  const root = opts.adopt && host ? host : el("div", "aegis");
  root.classList.add("aegis");
  root.dataset.theme = opts.theme;
  root.dataset.layout = opts.layout;
  if (root !== host) host.append(root);
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
