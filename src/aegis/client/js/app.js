// The aegis client: one home server, the servers it links, many sessions.
//
// The tabs are the open sessions of the home server and of every server it
// links (each one's `sessions` channel), the same in every browser. Their order
// and which one is focused belong to this browser: the order in localStorage,
// the focus in the URL hash. A session is known by its key: its log id at
// home, `<server>/<log_id>` on a linked server (links.py).

import { byId } from "./dom.js";
import { Transcript } from "./transcript.js";
import { artifactStage, btwFolds, entryFiles, fadeTables, fileUrl, paneFile, refreshApprovals, refreshWorkflows, setApprovals, setDocker, setFileBase, setOpener, setSecrets, setSpeaker, setWorkflows, syncDock, tickWorkflows, workflowLocked, workflowTitle } from "./entries.js";
import { setScroller } from "./highlight.js";
import { Viewer, peekRoots } from "./viewer.js";
import { Canvas } from "./canvas.js";
import { copyRow, installCopy, rowText } from "./copy.js";
import { Find } from "./find.js";
import * as artifacts from "./artifacts.js";
import { TabOrder } from "./tabs.js";
import { setWho, who } from "./me.js";
import { byNeed, card as fleetCard } from "./fleet.js";
import { installKeys, renderKeys } from "./keys.js";
import { Palette } from "./palette.js";
import { HeaderPart } from "./parts/header.js";
import { SidebarPart } from "./parts/sidebar.js";
import { CommandsPart } from "./parts/commands.js";
import { DictationPart } from "./parts/dictation.js";
import { SpawnPart } from "./parts/spawn.js";
import { FleetPart } from "./parts/fleet.js";
import { ViewsPart } from "./parts/views.js";
import { TerminalPart } from "./parts/terminal.js";
import { BrowserPart } from "./parts/browser.js";
import { factIcon, glyph, gorgoneion, icon, LABEL } from "./glyphs.js";
import * as cmenu from "./menu.js";
import { attachTabCard } from "./tabcard.js";
import { groupKey } from "./groups.js";
import { clampToRow, insertIntoComposer, quoted, rangeText } from "./quote.js";
import { closeMonitorCard, tickMonitors } from "./monitors.js";
import { CallTitle } from "./toolverb.js";
import { closeFeedCard, tickFeeds } from "./feeds.js";
import { Settings } from "./settings.js";
import { Journal } from "./journal.js";
import { Usage } from "./usage.js";
import { Knowledge } from "./knowledge.js";
import { FilePicker, Files, filesHash, parseTarget } from "./files.js";
import { Schedules } from "./schedules.js";
import { Help } from "./help.js";
import { listenForOpen, pingToast, redrawFavicon, setTitle as setPageTitle, updatePing } from "./ping.js";
import { ask, cancelAsk, choose } from "./dialog.js";
import "./pick.js";
import { Attachments } from "./attach.js";
import { setToastOpener, toast } from "./toast.js";
import { Speaker } from "./speak.js";
import { TermView } from "./termview.js";
import { BrowserTab, drawBrowser, mountBrowser } from "./browserview.js";
import { applyUpdate, checkForUpdate, setAutoApply, startWorker, takeBuild } from "./appworker.js";
import { closeInstallDialog } from "./install.js";
import { checkRotation, closePushPanel, openPushPanel, setupPush } from "./pushpanel.js";
import { clearDevice, loadFleet, loadTranscript, saveFleet, saveTranscript } from "./devicestore.js";
import { markNeedsNet, offline } from "./net.js";
import { outbox } from "./outbox.js";
import { idleFor, lifecycle } from "./lifecycle.js";
import { serverNow } from "./clock.js";

// The full layout's boot: the mount calls this once its markup is in the root
// (js/layouts/full.js), so a page that loads this module pays for its imports
// only when it asks for the full layout. `app` is the mount that owns the
// root, the socket and the state every view below reads.
export function composeFull(app) {
const root = app.root;

// Everything this layout hooked into the page or the socket, and takes down
// with its root (m6). A listener on a node inside the root dies with the node;
// these are the ones that outlive it, and the controllers the layout built are
// not the mount's `parts`, so their own destroy runs here too. Each helper
// records its undo, and the function this returns runs them all in order.
const teardown = [];
const listen = (target, name, fn, opts) => {
  target.addEventListener(name, fn, opts);
  teardown.push(() => target.removeEventListener(name, fn, opts));
};
const channel = (...args) => {
  const off = app.conn.subscribe(...args);
  teardown.push(off);
  return off;
};
const every = (ms, fn) => {
  const id = setInterval(fn, ms);
  teardown.push(() => clearInterval(id));
  return id;
};
const observe = (observer, target) => {
  observer.observe(target);
  teardown.push(() => observer.disconnect());
  return observer;
};

// Whether this mount owns the document. Only index.html's full layout does;
// a host that embeds the layout keeps its own title, favicon, theme attribute,
// service worker, push and URL hash (m5). Everything guarded by `page` below
// is a page-level effect a host must not inherit.
const page = app.opts.page === true;
// The router's source and sink: the URL on the page, this mount's state in a
// host. A host may route with its own hash; aegis never reads or writes it.
const hashNow = () => (page ? location.hash : app.state.hash || "");
const setHash = (hash, push = false) => {
  if (!page) {
    app.state.hash = hash;
    return;
  }
  if (push) history.pushState(null, "", hash);
  else history.replaceState(null, "", hash);
};
// A host's document is not ours to rename: the page's title is off limits.
const setTitle = page ? setPageTitle : () => {};

const phoneHeader = matchMedia("(max-width:760px)"); // the one-row header (client.css)

// Every node below is found under the root the mount built; the page keeps no
// markup of its own to ask for.
const $ = (id) => byId(root, id);

// The header is a part (b2): it builds the markup index.html used to hold and
// owns the strip's folds, the quiet marks and the switcher. The page hands it
// the actions only the router and the Fleet know.
const header = new HeaderPart(app, root, {
  open: (id) => openSession(id),
  openCard: (key) => openCard(key),
  close: (id) => closeById(id),
  move: (keys, before) => {
    order.move(keys, before);
    onSessions();
  },
  here: () => app.conn.server || localStorage.getItem("aegis.server") || "",
  onGroup: (key) => go(`#g=${key}`),
  groupKey: () => (route().view === "group" ? route().id : null),
  focusId: () => tabFocus(route()),
  go: (hash) => go(hash),
  hashOf: (key) => hashOf(key),
  spawn: () => spawn.open(),
  files: () => go(filesView?.path ? filesHash(filesView.path) : "#files"),
  side: () => toggleSide(),
  push: (bell, anchor) => openPushPanel(phoneHeader.matches ? anchor : bell),
  menu: (key) => sessionMenu(key),
  groupMenu: (key) => groupMenu(key),
  join: (keys, group) => moveToGroup(keys, group),
  leave: (keys) => moveToGroup(keys, null),
  renameGroup: (key, name) => renameGroup(key, name),
  card: (m) => fleetCard(m, openCard, (k) => app.state.sessions.get(k) && closeSession(app.state.sessions.get(k)), app.t),
});
// The header is the grid's first row, under the connection banner and above
// the views (the layout owns the order in b8).
root.insertBefore(header.el, root.querySelector("main.view"));

// The session panel is a part (b3): it builds the grip and the aside
// index.html used to hold, owns the card, the plan, repos, journal, usage,
// quota, host, the actions, the version/update card and the drawer. The page
// hands it the session in front and the operations only the router, the Fleet
// and the transcript know. A host may ask for a full layout without it
// (sidebar:false, m6): then there is no part, and every call below is a
// no-op through `sidebar?.`.
const sidebar = app.opts.sidebar === false ? null : new SidebarPart(app, root.querySelector(".v-session"), {
  focused: () => focused(),
  route: () => route(),
  view: () => root.dataset.view,
  go: (hash) => go(hash),
  open: (key) => openSession(key),
  close: (s) => closeSession(s),
  reopen: () => {
    const r = route();
    if (r.view === "read") reopen(r.id);
  },
  sleep: (key) => sleepKey(key),
  quiet: (key, on) => quietKey(key, on),
  autoclose: (key, on) => autocloseKey(key, on),
  autohandoff: (key, on) => autohandoffKey(key, on),
  continueKey: (key) => continueKey(key),
  restart: (key) => restartKey(key),
  keyOf: (server, id) => app.keyOf(server, id),
  forKey: (key) => app.forKey(key),
  callFor: (key, op, params) => app.callFor(key, op, params),
  locked: (s) => workflowLocked(s),
  measureNet: (server) => measureNet(server),
  revealRow: (id) => revealToolRow(id),
  revealEntry: (source) => transcript.reveal(source),
  closeMonitorCard: () => closeMonitorCard(),
  closeFeedCard: () => closeFeedCard(),
  drawer: () => drawerMode.matches,
  setVersionTop: (nodes, title) => header.setVersion(nodes, title),
  archiveInvalidate: () => {
    fleet.invalidateArchive();
  },
});

// The views that are not the Fleet, a session or the new-session form are
// parts too (b7): each builds its <main class="view"> and hands its module the
// section inside it. The key list, the command palette and the file picker are
// the overlays it builds beside them. They go ahead of the session's view so
// the markup keeps index.html's order until the full layout builds it (b8).
const views = new ViewsPart(app, root, { before: root.querySelector(".v-session") });

// A terminal and a browser each have a view of their own; their controllers
// take the part's nodes and never ask the page.
const terminalPart = new TerminalPart(app, root);
const terminal = new TermView(terminalPart);
const browserPart = new BrowserPart(app, root);

// The Fleet is a part (b6): it builds the markup index.html used to hold and
// owns the cards, the band, the linked servers and the archive. The page hands
// it what only the router, the card menu and the network know.
const fleet = new FleetPart(app, root, {
  open: (key) => openCard(key),
  close: (key) => closeById(key),
  go: (hash) => go(hash),
  read: (key) => go(`#read=${key}`),
  reopen: (id) => reopen(id),
  seen: () => seenText(),
  measure: (server) => measureNet(server),
  attachCardMenu: (box) => attachCardMenu(box),
  attachMenu: (box, items, opts) => cmenu.attach(box, items, opts),
});

// The state the views share lives on the mount (mount.js's MountState). Quota
// is one subscription for the page: the band and the sidebar both draw it.
// Host is subscribed only while a view shows it, the Fleet every server's and a
// session its own server's, so a server nobody watches samples nothing.
let quotaDrawnFor = null; // the view the quota rows were last drawn for
const nowS = serverNow;

// -- theme ----------------------------------------------------------------
// This browser's choice, picked in Settings; index.html applies it before the
// first paint.
const THEMES = [
  { value: "ink", label: "Ink" },
  { value: "logbook", label: "Logbook" },
  { value: "electric", label: "Electric" },
  { value: "phosphor", label: "Phosphor" },
  { value: "blueprint", label: "Blueprint" },
  { value: "amphora", label: "Amphora" },
  { value: "riso", label: "Riso" },
  { value: "solari", label: "Solari" },
  { value: "aizome", label: "Aizome" },
  { value: "poster", label: "Poster" },
];
function setTheme(name) {
  // A host's page keeps its own theme attribute and favicon (m5); the mount
  // restyles its own root either way.
  if (page) document.documentElement.dataset.theme = name;
  root.dataset.theme = name;
  localStorage.setItem("aegis.theme", name);
  if (page) redrawFavicon();
  artifacts.theme();
  pushChromeTheme();
}

// The paired browsers (chrome.py): Settings → Chrome lists them, the Fleet
// shows each as a card, and their popups draw in this browser's theme.
function pushChromeTheme() {
  if (app.state.chrome?.devices?.length) app.conn.call("chrome.theme", { vars: artifacts.themeVars() }).catch(() => {});
}
function onChrome(snap) {
  const before = app.state.chrome?.devices?.length ?? 0;
  app.state.chrome = snap || { devices: [] };
  settings.onChrome(app.state.chrome);
  fleet.renderChrome();
  sidebar?.drawChrome();
  if (app.state.chrome.devices.length > before) pushChromeTheme();
}

// -- state ----------------------------------------------------------------
// Sessions, links, remote servers, quota, host, the tab order, the shown key
// and the kept transcripts live on the mount (mount.js, "The state for the
// page"); every read and write below goes through app.state. What stays here
// is the page's drawing memo, which describes no data the server or device
// keeps.
const order = new TabOrder();

// A docked browser's key: its pane holds the id the browser's own server
// knows it by, and the canvas is the shown session's, on that server (#970).
const paneKey = (id) => app.keyOf(app.forKey(app.state.shown || "").server, id);
// Where a browser's views find its card and the sessions it can be given to.
// The mount names these, so a second mount on the page cannot overwrite them.
const browserCard = (id) => (app.state.sessions.has(paneKey(id)) ? termCard(paneKey(id)) : null);
const browserSources = {
  card: browserCard,
  sessions: () => local().filter((m) => !isTool(m) && m.state !== "asleep"),
};
const browserTab = new BrowserTab(browserPart.host, browserSources);
// The last session shown in this browser tab, where Files' quotes go; a
// reload of the Files view keeps it.
let lastShown = sessionStorage.getItem("aegis.lastShown");
let warmed = null; // the parked (asleep, idle) session whose first keystroke asked for session.warm
let landUnread = null; // the tab Alt+J opened, whose transcript lands on its first unread
let unsubTranscript = null;
let workingSince = null;
// The turn's new input and output tokens (session.turn_tokens), which the
// working row's counters ease toward (#938).
let workingTokens = [0, 0];
let workingShown = [0, 0];
let booted = false;
const transcript = new Transcript($("tr"), $("entries"), $("jump"), {
  // A failed report is retried by the next tick: the ids stay unread in the view.
  // One call carries at most 500 ids, the operation's cap.
  onRead: (ids) => {
    if (!app.state.shown) return;
    for (let i = 0; i < ids.length; i += 500) {
      const batch = ids.slice(i, i + 500);
      app.callFor(app.state.shown, "session.read", { ids: batch }).catch(() => batch.forEach((id) => transcript.sent.delete(id)));
    }
  },
  onSelect: drawNav,
  onReply: (id) => speaker.arrived(id),
  // In tab mode with another tab open the transcript is laid out but hidden.
  seen: () => canvas.transcriptShown(),
  loadDetail: (ids) => {
    const id = app.state.shown;
    return app.callFor(id, "transcript.detail", { ids }).then((got) => (app.state.shown === id ? got : []));
  },
  // Only this server's replies carry pending refs (entries.js), so a linked
  // server is never asked.
  statPaths: (entryId) => {
    const id = app.state.shown;
    return app.callFor(id, "path.stat", { entry_id: entryId }).then((got) => {
      if (app.state.shown !== id) throw new Error("another session is shown");
      return got;
    });
  },
});
artifacts.setup({
  call: (op, params, key) => app.callFor(key || app.state.shown, op, params),
  entry: (id) => transcript.entries.get(id),
  key: () => app.state.shown,
  point: (frame, params) => pagePoint(frame, params),
  dictation: () => dictation.engine,
  toast: (message) => toast(message, { kind: "error" }),
  waitOnline: () => waitOnline(),
  local: (key) => !app.forKey(key).server,
});
const pointing = { items: (ctx) => pointItems(ctx), send: (key, text) => toComposer(key, text) };
const viewer = new Viewer($("viewer"), { entry: (id) => transcript.entries.get(id), point: pointing });
// Open on a docked item shows its pane, so a page never runs in the viewer
// and a pane at once. An older version of a docked file opens in the viewer:
// the pane shows the newest.
// One in another browser window focuses or opens that window's tab.
// The pane id of what the viewer shows: a page's, a run's card's, or the
// shown file's when it is the newest of its key.
const itemId = (item) => {
  if (item.artifact) return item.artifact;
  if (item.workflow) return `wf-${item.workflow}`;
  const f = item.files?.[item.at];
  return f && !f.replaced ? f.key : null;
};
setOpener((item) => {
  const id = itemId(item);
  const away = id && canvas.elsewhere(id);
  if (away) openWindow(app.state.shown, away);
  else if (!(id && canvas.flash(id))) viewer.open(item);
});
// The newest stored file with a key, for its pane: sent files, input
// attachments, and files opened from tool rows or reply paths. Walked only
// when a pane draws.
function latestFile(key) {
  const es = [...transcript.entries.values()];
  for (let i = es.length - 1; i >= 0; i--) {
    const f = entryFiles(es[i]).find((x) => x.key === key && !x.replaced);
    if (f) return f;
  }
  return undefined;
}
// The canvas: docked pages and files beside the transcript (js/canvas.js).
const canvas = new Canvas($("canvas"), {
  transcriptPane: $("tr-pane"),
  entryOf: (id) => transcript.entries.get(id),
  latestFile,
  call: (op, params) => app.callFor(app.state.shown, op, params),
  // A card's Dock button hides while its item is docked, and a file card's
  // page swaps its preview for a line; a docked page's card swaps its frame
  // by itself (entries.hold).
  onChange: () => syncDock(),
  refuse: (message) => toast(message, { kind: "error" }),
  openWindow: (n, tab) => openWindow(app.state.shown, n, tab),
  // A browser the session drives, docked: its own view of the stream.
  browserCard,
  mountBrowser: (host, id) => {
    const key = paneKey(id);
    return app.state.sessions.has(key)
      ? mountBrowser(host, id, termCard(key), { conn: app.conn, server: app.forKey(key).server, sources: browserSources })
      : null;
  },
});
// Dock on a card or the viewer's bar. The viewer lets go of what it docks.
// The viewer lets go of what it docks or pops out.
function leaveViewer(id) {
  const item = viewer.isOpen ? viewer.items.get(viewer.key) : null;
  if (item && (item.workflow ? `wf-${item.workflow}` : item.artifact || item.files[item.at].key) === id) viewer.close();
}
setDocker({
  dock: (id) => {
    leaveViewer(id);
    canvas.dock(id);
  },
  can: (id) => canvas.writable && !canvas.has(id) && !canvas.elsewhere(id),
  docked: (id) => canvas.has(id) || canvas.elsewhere(id) || false,
  show: (id) => canvas.flash(id),
  open: (n) => openWindow(app.state.shown, n),
  pop: (id) => {
    leaveViewer(id);
    canvas.popOut(id);
  },
  canPop: () => canvas.writable && !canvas.merged(),
  frames: () => route().view !== "window",
});
// Window n of a session's canvas, in the browser tab named for it: focused if
// this browser has that tab open, opened if not. Given `tab`, a tab opened
// for another number is renamed and pointed at window n instead.
function windowUrl(key, n) {
  return `${location.pathname}${location.search}#w=${n}:${key}`;
}
function openWindow(key, n, tab = null) {
  const name = `aegis-${key}-w${n}`;
  if (!tab) return window.open(windowUrl(key, n), name);
  tab.name = name;
  tab.location.replace(windowUrl(key, n));
  return tab;
}
// The shown session's canvas, from its card: window 1 in the session view,
// window n in a window tab. A page the viewer shows that is now docked
// leaves the viewer first, so it never runs in both.
function showCanvas(s) {
  const lay = s.layout || null;
  const docked = new Set((lay?.windows || []).flatMap((w) => w.columns.flatMap((c) => c.panes.map((p) => p.id))));
  const item = viewer.isOpen ? viewer.items.get(viewer.key) : null;
  if (item?.artifact && docked.has(item.artifact)) viewer.close();
  const r = route();
  canvas.show(s.key, lay, { n: r.view === "window" ? r.n : 1, writable: !s.off && s.state !== "archived" });
  syncDock();
}
setScroller($("tr"));
observe(new ResizeObserver(() => fadeTables($("entries"))), $("tr"));
const copyDeps = {
  entry: (id) => transcript.entries.get(id),
  output: (id) => app.callFor(app.state.shown, "transcript.output", { id }).then((r) => r.text),
  detail: (id) => app.callFor(app.state.shown, "transcript.detail", { ids: [id] }).then((got) => got[0]),
};
installCopy($("entries"), copyDeps);
installCopy($("viewer"), copyDeps);
const find = new Find($("find"), transcript, (q) => app.callFor(app.state.shown, "transcript.search", { q }).then((r) => r.ids));
$("find-prev").append(icon("up"));
$("find-next").append(icon("down"));
$("find-close").append(icon("close"));
// The navigator: previous / next agent message, the position, and the latest.
$("nav-recap").prepend(icon("sparkle"));
$("nav-needs").prepend(glyph("needs_you"));
$("nav-last").append(icon("start"));
$("nav-up").append(icon("up"));
$("nav-down").append(icon("down"));
$("jump").append(icon("latest"));
$("nav-side").append(icon("panel"));
$("attach").append(icon("clip"));
$("send").append(icon("send"));
$("interrupt").append(icon("stop"));
$("replies-g").append(icon("prompt"));
$("queued-g").append(icon("prompt"));
$("working-g").append(icon("think"));
$("waking-g").append(icon("think"));
$("coldnote-g").append(icon("wait"));
$("closing-g").append(glyph("done"));
$("boot-mark").append(gorgoneion());
$("nav-up").addEventListener("click", () => transcript.message(-1));
$("nav-down").addEventListener("click", () => transcript.message(1));
$("nav-pos").addEventListener("click", () => transcript.firstUnread());
$("nav-last").addEventListener("click", () => transcript.lastMessage());
$("nav-needs").addEventListener("click", () => nextNeed());
$("nav-recap").addEventListener("click", () => askRecap(true));
// Every redraw of the transcript re-marks the selection, which asks for the
// navigator, so it is drawn at most once a frame: position() walks every entry.
let navFrame = 0;
function drawNav() {
  if (!navFrame) navFrame = requestAnimationFrame(drawNavNow);
}
// Shown with any entry, so the latest button is there before the first agent
// message; with none, the position is empty and the arrows are off. The label
// is only "3/4", so the pill fits a phone; the words are its name. The needs
// button counts the other sessions Alt+J would open. Unchanged values are not
// written back.
let navDrawn = {};
function drawNavNow() {
  navFrame = 0;
  const { index, total, unread } = transcript.position();
  const r = route();
  const needs = byNeed(app.state.ordered).filter((m) => r.view !== "session" || m.key !== r.id).length;
  const now = {
    hidden: !transcript.entries.size,
    text: total ? `${index}/${total}` : "",
    label: total ? `${unread ? `${unread} unread · ` : ""}message ${index} of ${total}` : "",
    unread: unread > 0,
    off: !total,
    needs,
  };
  if (now.hidden !== navDrawn.hidden) $("nav").hidden = now.hidden;
  if (now.text !== navDrawn.text) $("nav-pos").textContent = now.text;
  if (now.label !== navDrawn.label) {
    $("nav-pos").setAttribute("aria-label", now.label);
    $("nav-pos").title = `${now.label}. First unread (Alt+U)`;
  }
  if (now.unread !== navDrawn.unread) $("nav-pos").classList.toggle("unread", now.unread);
  if (now.off !== navDrawn.off) $("nav-up").disabled = $("nav-down").disabled = $("nav-last").disabled = now.off;
  if (now.needs !== navDrawn.needs) {
    $("nav-needs").disabled = !needs;
    $("nav-needs-n").textContent = needs ? String(needs) : "";
  }
  navDrawn = now;
}
// The fold level: this browser's choice, for every tab. z steps through
// everything shown, the work folded, and all but the messages.
const FOLDS = ["Everything shown", "Tool calls and thinking folded", "Only messages shown"];
$("nav-fold").prepend(icon("fold"));
function foldLevel(level) {
  transcript.setFoldLevel(level);
  $("nav-fold").dataset.level = String(level);
  $("nav-fold").title = `${FOLDS[level]}. Press for: ${FOLDS[(level + 1) % 3].toLowerCase()} (Z)`;
  localStorage.setItem("aegis.foldLevel", String(level));
}
foldLevel(Number(localStorage.getItem("aegis.foldLevel")) % 3 || 0);
$("nav-fold").addEventListener("click", () => foldLevel((transcript.foldLevel + 1) % 3));
// Reading replies aloud (speak.js): one speaker for this browser. The
// composer's toggle, beside the mic, follows a session, reading each new reply as it arrives;
// which sessions are followed is this tab's, kept across a reload.
const speaker = new Speaker({
  prepare: () => app.conn.call("speech.prepare"),
  row: (id) => transcript.nodes.get(id) || null,
  onError: (message) => toast(`Read aloud: ${message}`, { kind: "error" }),
});
setSpeaker(speaker);
const followed = new Set(JSON.parse(sessionStorage.getItem("aegis.speakFollow") || "[]"));
$("speak-follow").append(icon("speak"));
function drawSpeak() {
  const on = !!app.state.shown && followed.has(app.state.shown);
  $("speak-follow").classList.toggle("on", on);
  $("speak-follow").setAttribute("aria-pressed", String(on));
  $("speak-follow").title = on ? "Stop reading new replies aloud" : "Read new replies aloud";
  speaker.setFollowing(on);
}
$("speak-follow").addEventListener("click", () => {
  if (!app.state.shown) return;
  if (followed.has(app.state.shown)) followed.delete(app.state.shown);
  else followed.add(app.state.shown);
  sessionStorage.setItem("aegis.speakFollow", JSON.stringify([...followed]));
  drawSpeak();
});
$("entries").addEventListener("click", (ev) => {
  const b = ev.target.closest("button.speak");
  const row = b?.closest(".row.prose");
  if (!row) return;
  ev.preventDefault();
  speaker.toggle(row);
});
// Terminals and browsers are tools, not agents: the "terminals" kind shows both.
const isTool = (m) => m.kind === "term" || m.kind === "browser";

// -- routing: #fleet, #new[?<fill>], #settings, #journal, #usage, #knowledge[=<name>], #files[=<path>[:line]], #schedules, #help[=<page>], #s=<key>, #t=<key>, #read=<key>, #w=<n>:<key>, #g=<group key> --
// A help ref is URI-encoded; a hand-typed malformed one is a page name nobody has.
const unhash = (s) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};
function route(hash = hashNow()) {
  const h = hash.slice(1);
  // A row's link (its menu's Copy link) names the entry to reveal after "&at=".
  if (h.startsWith("s=")) {
    const [id, at] = h.slice(2).split("&at=");
    return at ? { view: "session", id, at } : { view: "session", id };
  }
  if (h.startsWith("t=")) return { view: "term", id: h.slice(2) };
  if (h.startsWith("b=")) return { view: "browser", id: h.slice(2) };
  if (h.startsWith("read=")) return { view: "read", id: h.slice(5) };
  // A group's page (#1039): the Fleet's cards over its members only.
  if (h.startsWith("g=")) return { view: "group", id: h.slice(2) };
  // Window n of a session's canvas, a browser tab of its own (js/canvas.js).
  const w = /^w=(\d+):(.+)$/.exec(h);
  if (w) return { view: "window", id: w[2], n: Number(w[1]) };
  // A link or a share that fills the New session form (#735): the query after
  // "new?" is read once by render, which drops it from the URL.
  if (h === "new") return { view: "spawn" };
  if (h.startsWith("new?")) return { view: "spawn", fill: new URLSearchParams(h.slice(4)) };
  if (h === "settings") return { view: "settings" };
  if (h === "journal") return { view: "journal" };
  if (h === "usage") return { view: "usage" };
  if (h === "knowledge" || h.startsWith("knowledge=")) return { view: "knowledge", id: unhash(h.slice(10)) };
  if (h === "files" || h.startsWith("files=")) return { view: "files", id: unhash(h.slice(6)) };
  if (h === "schedules") return { view: "schedules" };
  if (h === "help" || h.startsWith("help=")) return { view: "help", id: unhash(h.slice(5)) };
  return { view: "fleet" };
}

// Navigation renders in the same step as the URL changes. Setting
// location.hash would render later, on hashchange, and anything typed in
// between was wiped when the new tab's draft loaded (#128's CI failure).
// Back and Forward, and a hand-edited URL, still arrive through hashchange.
// The Files view (files.js); set once the connection exists, below.
let filesView = null;
let filePicker = null;

function go(hash) {
  // Unsaved edits in the Files view ask first (Save, Discard, Cancel).
  if (filesView?.leaving(route(hash))) {
    filesView.confirmLeave().then((ok) => ok && go(hash));
    return;
  }
  if (hashNow() !== hash) setHash(hash, true);
  render();
}

// A host's page may route with its own hash: only the standalone page listens.
if (page) {
  listen(window, "hashchange", render);
  listen(window, "popstate", render);
}
// The layout draws real anchors with hash hrefs (a terminal's Show link, a
// continued-by link, a message's #s= link). In a host they route this mount,
// not the page's URL: one listener for all of them, since a part that draws a
// link does not know whether it is on the page or in an embed (m5).
if (!page) {
  root.addEventListener("click", (ev) => {
    if (ev.defaultPrevented || ev.button !== 0) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const a = ev.target.closest?.('a[href^="#"]');
    // Only a real HTML anchor inside this mount; a <use> in the glyph sprite
    // has a hash href but is not a link.
    if (!a || !root.contains(a) || !(a instanceof HTMLAnchorElement)) return;
    if (a.target && a.target !== "_self") return;
    ev.preventDefault();
    setHash(a.getAttribute("href"), true);
    render();
  });
}

function show(view, text) {
  root.dataset.view = view;
  if (text) $("boot-text").textContent = text;
}

// -- signing in: the socket said 4401, so this browser has no valid cookie -----
function showLogin() {
  show("boot", "This browser is not signed in to this server.");
  $("login").hidden = false;
  if (new URLSearchParams(location.search).has("refused")) {
    $("login-error").textContent = "That token was refused. Paste the one aegis serve printed.";
    if (page) setHash(location.pathname + location.hash);
  }
  $("login-token").focus();
}

$("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  const r = await fetch("/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token: $("login-token").value.trim() }),
  });
  if (r.ok) location.reload();
  else $("login-error").textContent = "That token was refused.";
});

// -- the connection -----------------------------------------------------------
let wasClosed = false; // a reconnect checks for a new build
// The build this page is (web.py writes it in), against the one the server
// names in each welcome: they differ after an upgrade (#499).
// A <meta> lives in the page's head and nothing inside the mount can stand in
// for it: an embedded full starts with no build and takes it from the welcome.
const PAGE_BUILD = page ? document.querySelector("meta[name=aegis-build]")?.content || "" : "";

// The banner while the connection is down: a state, so it stays at the top of
// the page rather than a toast. A socket that comes back within 3 s never
// shows it; one that stays down counts down to the next attempt.
const BANNER_AFTER_MS = 3000;
let closedSince = 0;
let bannerTimer = null;
function drawBanner() {
  const down = closedSince > 0 && Date.now() - closedSince >= BANNER_AFTER_MS;
  $("netbanner").hidden = !down;
  if (!down) return;
  const s = Math.ceil((app.conn.retryAt - Date.now()) / 1000);
  const retry = app.conn.retryAt && s > 0 ? `retrying in ${s} s` : "retrying now";
  $("netbanner-t").textContent = [seenText(), retry].filter(Boolean).join(" · ");
}

// When this device last saw the server live: the moment the socket last
// closed, or the saved Fleet's saved_at at boot. 0 while it never has.
let seenAt = 0;
let live = false; // the socket is open
function seenText() {
  if (!seenAt) return "";
  const m = Math.floor((Date.now() - seenAt) / 60000);
  return m < 1 ? "seen just now" : m < 60 ? `seen ${m} min ago` : `seen ${Math.floor(m / 60)} h ago`;
}
// Offline (the socket is not open), the page draws what the device kept and
// what needs the server is disabled.
function markOffline(state) {
  if (state === "open") {
    live = true;
    delete root.dataset.offline;
  } else {
    if (live) seenAt = Date.now();
    live = false;
    root.dataset.offline = "";
  }
  markNeedsNet();
  // The band's line says how old the device copy is while offline: opening or
  // closing the socket changes it, not only the next snapshot.
  if (root.dataset.view === "fleet") fleet.band();
}

// The token no longer works: what this device kept goes before the sign-in
// shows, and no saved card stays drawn behind it.
let refused = false;
function signedOut() {
  refused = true;
  clearTimeout(fleetSave);
  clearTimeout(trSave);
  fleetSave = trSave = null;
  app.state.sessions.clear();
  app.state.kept.clear();
  app.state.ordered = [];
  transcript.clear();
  header.render([], null, "boot");
  clearDevice().then(showLogin);
}
function bannerState(state) {
  if (state === "closed" && !closedSince) {
    closedSince = Date.now();
    bannerTimer = setInterval(drawBanner, 1000);
  }
  // A refused token or protocol is not a connection problem: they say so elsewhere.
  if (state === "open" || state === "unauthorized" || state === "version") {
    closedSince = 0;
    clearInterval(bannerTimer);
    bannerTimer = null;
    $("netbanner").hidden = true;
  }
}
// This device's push against the server's row (pushpanel.js): on every
// connect, and whenever the page comes back in front.
const checkPush = () => {
  // Push belongs to the page that registered it: a host has its own (m5).
  if (!page) return;
  checkRotation().catch((e) => console.warn("aegis: checking push failed", e));
};

// The page's socket is the mount's (acquire, from mount.start): this only
// hears every state it passes through and draws the header, the banner and the
// signed-out page.
function onState(state, server) {
  header.setConn(state, server);
  // A snapshot after the socket came back is authoritative, even if the
  // server's store is shorter than what this tab held (a restart cuts the tail).
  transcript.connection(state);
  // For the band offline, where the welcome that names the server is out of reach.
  if (state === "open" && server) localStorage.setItem("aegis.server", server);
  // What arrived before a close is still the server's latest: kept at once.
  if (state === "closed" && live) {
    if (fleetSave) flushFleet(true);
    if (trSave) flushTranscript(true);
  }
  markOffline(state);
  drawOfflineNotice();
  bannerState(state);
  if (state === "unauthorized") signedOut();
  if (state === "version") {
    show(
      "boot",
      // With a worker, a reload would serve this same build again: the new
      // one is fetched and taken as soon as it is ready (appworker.js).
      navigator.serviceWorker
        ? "This page and the server speak different protocol versions. Updating aegis…"
        : "This page and the server speak different protocol versions. Reload the page.",
    );
    setAutoApply();
  }
  if (state === "closed") wasClosed = true;
  if (state === "open") {
    if (app.conn.build && PAGE_BUILD && !PAGE_BUILD.startsWith("__") && app.conn.build !== PAGE_BUILD) takeBuild(app.conn.build);
    else if (wasClosed) checkForUpdate();
    wasClosed = false;
    setWho(app.conn.me, app.conn.owner);
    checkPush(); // after setWho: the rows are this token's
    // Whether this browser runs on the server's desktop (Open natively).
    if (app.conn.native) root.dataset.native = "";
    else delete root.dataset.native;
    spawn.loadAgents();
    sidebar?.loadVersion();
    filesView?.online();
  }
}
app.on("connection", ({ state, server }) => onState(state, server));
// Retry at once when the network or the app comes back, or when asked.
$("netbanner-retry").addEventListener("click", () => app.conn.retryNow());
listen(window, "online", () => app.conn.retryNow());
listen(document, "visibilitychange", () => {
  if (document.hidden) return;
  app.conn.retryNow();
  if (app.conn.open) checkPush(); // otherwise the connect does
});
const settings = new Settings(app.conn, views.settings, {
  themes: THEMES,
  setTheme,
  speaker,
  lastSession: () => lastShown,
  openSession: (id) => openSession(id),
});
let landEntry = null; // {key, id}: reveal this entry once its transcript lands
const journal = new Journal(app.conn, views.journal, {
  onOpen: (r) => {
    landEntry = r.source ? { key: r.log_id, id: r.source } : null;
    go(r.open ? `#s=${r.log_id}` : `#read=${r.log_id}`);
  },
});
// The Knowledge view routes its rows and Back through the URL, so the
// browser's Back closes an open item.
const knowledgeView = new Knowledge(app.conn, views.knowledge, {
  onOpen: (name) => go(`#knowledge=${encodeURIComponent(name)}`),
  onBack: () => go("#knowledge"),
});
// Alt+O's picker opens a file in the Files view; Shift+Enter in a new browser tab.
filePicker = new FilePicker(views.fpick, app.conn, (path, line, newTab) => {
  const hash = filesHash(path, line);
  if (newTab) window.open(location.pathname + location.search + hash, "_blank");
  else go(hash);
});
// Files' pointing writes to the session last shown in this window: the Files
// view itself shows none.
filesView = new Files(app.conn, views.files, {
  toast,
  go,
  onPick: () => filePicker.open(),
  point: { items: (ctx) => pointItems({ ...ctx, to: lastShown }), send: (text) => toComposer(lastShown, text) },
  // A Files row's menu (spec 06): paths are under the root, which is this
  // server's harness folder (roster.cwd).
  here: {
    insert: (path) => toComposer(lastShown, `\`${path}\``, { caret: true }),
    session: async (dir) => spawn.openAt(dir),
    terminal: (dir) =>
      app.conn.call("term.spawn", { profile: "shell", cwd: dir || "." }).then(
        (card) => go(`#t=${app.keyOf(null, card.id)}`),
        (e) => toast(e.message, { kind: "error" }),
      ),
  },
});
// The Help tab (help.js), one per window like Settings. Its code blocks copy
// as the transcript's do.
// Guided tour: what /tutorial <page> does, on default_agent, for any page. A
// failure shows as an error toast, since the Help tab belongs to no session.
const helpTab = new Help(app.conn, views.help, {
  go,
  onKeys: () => help(true),
  onStart: (id, tutorial) =>
    spawn.startGuide({ page: id }, "tutorial", tutorial).catch((e) => toast(e.message, { kind: "error" })),
  onAsk: (question, page) =>
    spawn.startGuide({ question, page }, "ask", false).catch((e) => toast(e.message, { kind: "error" })),
});
installCopy(views.help, copyDeps);
installCopy(views.files, copyDeps);
const sideJournalChanged = () => {
  journal.changed();
  // Away from the session view nothing refetches; the panel forgets what it
  // drew so the next render of any session does.
  sidebar?.journalChanged();
};
channel("journal", sideJournalChanged, sideJournalChanged);
// The usage page (usage.js): the channel says a turn was counted, the page
// asks again while it is shown.
const usage = new Usage(app.conn, views.usage, {
  onOpen: (r) => go(r.open ? `#s=${r.session}` : `#read=${r.session}`),
});
const usageChanged = () => usage.changed();
channel("usage", usageChanged, usageChanged);
// The Schedules view (schedules.js); its button counts them, red while one is
// paused by errors.
const schedules = new Schedules(app.conn, views.schedules, {
  go,
  sessions: (server) => (server ? on(server) : local()),
  onCount: (n, red) => header.setSchedules(n, red),
});
setSecrets((op, params) => app.callFor(app.state.shown, op, params));
// A card asks the server of the session it is shown in: names are per server.
const shownServer = () => (app.state.shown ? app.forKey(app.state.shown).server : null);
// A card a session here made across a link goes to the server it names.
// In a far session's transcript the name is that server's word about its own
// links, and undefined finds no schedule: it could otherwise put an Unpause for
// a schedule of this server or another link in front of a person.
const cardServer = (named) => (!named ? shownServer() : shownServer() ? undefined : named);
setApprovals({
  schedule: (name, named) => schedules.on(cardServer(named)).get(name) || null,
  call: (op, params, named) => app.conn.call(op, params, cardServer(named)),
});
const scheduleHandlers = (server) => [
  (list) => {
    schedules.onSnapshot(list, server);
    refreshApprovals();
  },
  (ops) => {
    schedules.onPatch(ops, server);
    refreshApprovals();
  },
];
channel("schedules", ...scheduleHandlers(null));

// The workflow runs (workflows.py), for their cards in the transcript and
// their canvas panes. A running card counts its times up once a second.
// `workflow-runs` has every run's summary, without the tree and the story a
// card draws: those were 96% of the bytes (#525). A card's run comes whole
// from `workflow:<run>`, subscribed while a card of it is in the page. A linked
// server's runs come the same way over its link (#489), each with its `server`,
// and a card's subscribe and calls go there.
const wfRuns = new Map(); // run -> its summary, with `server`: null for this one
const wfWhole = new Map(); // run -> {w: the run whole, or null until it lands; unsub}
function wfGet(run) {
  const summary = wfRuns.get(run);
  if (!summary) return null;
  let d = wfWhole.get(run);
  if (!d) {
    d = { w: null, unsub: null };
    wfWhole.set(run, d);
    // The run is the one asked for, whatever a far server says: a card's calls
    // are routed by it, and must not reach a run of this server.
    const land = (w) => {
      d.w = { ...w, run };
      wfChanged(new Set([run]));
    };
    d.unsub = channel(
      `workflow:${run}`,
      land,
      (ops) => {
        for (const op of ops) if (op.set) land(op.set);
      },
      undefined,
      undefined,
      summary.server,
    );
  }
  // Until it lands, the card draws from the summary, with no rows yet.
  return d.w || { ...summary, tree: [], story: [] };
}
// A run whose cards all left the page is no longer followed.
function wfForget() {
  for (const [run, d] of wfWhole) {
    if (root.querySelector(`.wfcard[data-run="${CSS.escape(run)}"], .wfdcard[data-run="${CSS.escape(run)}"]`)) continue;
    d.unsub();
    wfWhole.delete(run);
  }
}
every(10000, wfForget);
setWorkflows({
  get: wfGet,
  summary: (run) => wfRuns.get(run) || null,
  call: (op, params) => app.conn.call(op, params, wfRuns.get(params.run)?.server),
  view: (files, line, remote) => viewer.open({ files, at: 0, remote, glyph: "file", line }),
});
const locked = workflowLocked;
// A patch redraws only the cards of the runs it carries.
const wfChanged = (runs) => {
  refreshWorkflows(runs);
  canvas.syncStrip(); // a docked run's tab takes its title
  // The shown session may be a node of a run that just ended: its box unlocks.
  const s = app.state.sessions.get(app.state.shown);
  if (s?.workflow && (!runs || runs.has(s.workflow.run))) renderMeta(s);
};
// A far server cannot take over a run id already held by another server, and
// this server's own runs always win: a card's calls go where its row says.
function wfPut(w, server) {
  const had = wfRuns.get(w.run);
  if (server && had && had.server !== server) return false;
  wfRuns.set(w.run, { ...w, server });
  return true;
}
function wfHandlers(server) {
  return [
    (list) => {
      for (const [run, w] of wfRuns) if (w.server === server) wfRuns.delete(run);
      for (const w of list || []) wfPut(w, server);
      wfChanged();
    },
    (ops) => {
      const runs = new Set();
      for (const op of ops) {
        const w = op.upsert || (op.merge && wfRuns.has(op.merge.run) && { ...wfRuns.get(op.merge.run), ...op.merge });
        if (!w || !wfPut(w, server)) continue;
        // A whole run that is followed takes the summary's news too.
        const d = wfWhole.get(w.run);
        if (d?.w) d.w = { ...d.w, ...w };
        runs.add(w.run);
      }
      wfChanged(runs);
    },
  ];
}
channel("workflow-runs", ...wfHandlers(null));
every(1000, () => {
  if ([...wfRuns.values()].some((w) => w.status === "running")) tickWorkflows();
});

// Each server's `sessions` channel: a snapshot replaces that server's metas,
// patches upsert, merge (only the fields that changed) and remove them.
function sessionHandlers(server) {
  return [
    (metas) => {
      for (const [k, m] of app.state.sessions) if (m.server === server) app.state.sessions.delete(k);
      for (const m of metas || []) {
        const w = app.withKey(m, server);
        app.state.sessions.set(w.key, w);
      }
      if (server) app.state.remote.get(server).loaded = true;
      else booted = homeLive = true;
      onSessions();
      // The server is reachable again: what was written meanwhile goes out.
      outbox.flushNow((key) => app.forKey(key).server === server);
    },
    (ops) => {
      for (const op of ops) {
        if (op.upsert) {
          const w = app.withKey(op.upsert, server);
          const prev = app.state.sessions.get(w.key);
          if (!prev) setChanged = true;
          app.state.sessions.set(w.key, w);
          // The mount's working transition (m1): a full layout reports
          // turnStart/turnEnd for every session it knows (m6).
          app.noteTurn(w.key, prev, w);
          changed.add(w.key);
        } else if (op.merge) {
          const k = app.keyOf(server, op.merge.log_id);
          const held = app.state.sessions.get(k);
          if (!held) continue;
          const w = app.withKey({ ...held, ...op.merge }, server);
          app.state.sessions.set(k, w);
          app.noteTurn(k, held, w);
          changed.add(k);
        } else if (op.remove !== undefined) {
          app.state.sessions.delete(app.keyOf(server, op.remove));
          setChanged = true;
          // The server archives a session before it publishes the removal, so
          // this is the moment the archive gains a row, wherever it was closed.
          // A close carries the row; anything else reads the page again.
          if (op.archived) fleet.archiveGained({ ...op.archived, server: server ?? app.conn.server });
          else fleet.invalidateArchive();
        }
      }
      // A session added or removed redraws at once: a reply that navigates
      // to it (spawn, reopen) arrives right after this patch.
      if (setChanged) {
        setChanged = false;
        changed.clear();
        onSessions();
      } else {
        if (!frame) frame = requestAnimationFrame(flushSessions);
        // Not in the frame: a hidden tab runs no frames, and that is when the
        // ping matters.
        ping();
        keepFleet();
      }
    },
  ];
}
channel("sessions", ...sessionHandlers(null));

// The servers this one links. A link that comes back resubscribes everything
// on it, each with the revision it holds; one removed takes its sessions along.
function onLinks(list) {
  app.state.links = list || [];
  app.state.linksLoaded = true;
  app.state.linksArrived();
  settings.onLinks(app.state.links, app.conn.server);
  spawn.drawServerPick();
  schedules.onLinks(app.state.links, app.conn.server);
  const names = new Set(app.state.links.map((l) => l.name));
  for (const [name, r] of app.state.remote) {
    if (names.has(name)) continue;
    for (const u of r.unsubs) u();
    r.unsubHost?.();
    app.state.remote.delete(name);
    for (const [k, m] of app.state.sessions) if (m.server === name) app.state.sessions.delete(k);
    for (const [run, w] of wfRuns) if (w.server === name) wfRuns.delete(run);
    wfChanged();
  }
  for (const l of app.state.links) {
    let r = app.state.remote.get(l.name);
    if (!r) {
      r = { unsubs: [], quota: { providers: [] }, host: null, unsubHost: null, loaded: false, state: l.state };
      app.state.remote.set(l.name, r);
      r.unsubs.push(
        channel("sessions", ...sessionHandlers(l.name), undefined, undefined, l.name),
        channel("workflow-runs", ...wfHandlers(l.name), undefined, undefined, l.name),
        channel("schedules", ...scheduleHandlers(l.name), undefined, undefined, l.name),
        channel(
          "quota",
          (snap) => {
            r.quota = snap || { providers: [] };
            drawQuota();
          },
          (ops) => {
            for (const op of ops) if (op.set) r.quota = op.set;
            drawQuota();
          },
          undefined,
          undefined,
          l.name,
        ),
      );
    } else if (l.state === "linked" && r.state !== "linked") app.conn.resubscribe(l.name);
    r.state = l.state;
  }
  // A saved session of a server no longer linked will never be replaced.
  for (const [k, m] of app.state.sessions) if (m.stale && m.server && !names.has(m.server)) app.state.sessions.delete(k);
  for (const [k, m] of app.state.sessions) if (m.server) app.state.sessions.set(k, { ...m, off: app.isOff(m.server) });
  // Which servers are up changes what the archive can list: read it again.
  const sig = app.state.links.map((l) => `${l.name}:${l.state}`).join(",");
  if (sig !== app.state.linksSig) {
    app.state.linksSig = sig;
    fleet.invalidateArchive();
    if (booted && root.dataset.view === "fleet") fleet.loadArchive();
  }
  if (root.dataset.view === "fleet") watchHost("all");
  if (booted) onSessions();
}
channel(
  "links",
  onLinks,
  (ops) => {
    for (const op of ops) if (op.set) onLinks(op.set);
  },
  () => onLinks([]), // a server without links
);
channel(
  "chrome",
  onChrome,
  (ops) => {
    for (const op of ops) if (op.set) onChrome(op.set);
  },
);
channel(
  "quota",
  (snap) => {
    app.state.quota = snap || { providers: [] };
    drawQuota();
  },
  (ops) => {
    for (const op of ops) if (op.set) app.state.quota = op.set;
    drawQuota();
  },
);
channel(
  "config",
  (w) => settings.onHomeConfig(w),
  (ops) => {
    for (const op of ops) if (op.set) settings.onHomeConfig(op.set);
    spawn.loadAgents();
  },
);

// The Fleet this device saw last, drawn at once: with no connection it is all
// there is, and with one it shows until the live snapshot replaces it. A saved
// session carries `stale` until its server's snapshot arrives.
// The copy this device kept of the session the URL opens, read while the
// socket connects. follow() waits for it before it subscribes, and read only
// once the Fleet's snapshot had arrived it held the subscription back by about
// 40 ms on every cold load (#345). Any other first view leaves it unused.
const boot = route();
let early = ["session", "window", "read"].includes(boot.view) ? { id: boot.id, copy: loadTranscript(boot.id) } : null;

loadFleet().then((saved) => {
  if (!saved || booted || refused) return;
  seenAt = saved.saved_at;
  // An older client saved stopped and parked: read them as asleep (#479).
  for (const m of saved.metas) app.state.sessions.set(m.key, { ...lifecycle(m), stale: true });
  booted = true;
  onSessions();
});

// The Fleet is written to the device at most every SAVE_MS, SAVE_MS after the
// first change since the last write, and at once when the page goes hidden
// (Android may freeze it right after). Only while the socket is open, so
// saved cards are never written back as fresh ones.
const SAVE_MS = 2000;
let fleetSave = null;
function keepFleet() {
  if (!fleetSave && app.conn.open) fleetSave = setTimeout(flushFleet, SAVE_MS);
}
// `closing`: the socket has just closed, and what the page holds is still
// what it last carried.
function flushFleet(closing = false) {
  clearTimeout(fleetSave);
  fleetSave = null;
  if ((app.conn.open || closing) && homeLive) saveFleet([...app.state.sessions.values()].filter((m) => !m.stale));
}
// The shown transcript, likewise, after each snapshot, delta and patch.
let trSave = null;
function keepTranscript() {
  if (!trSave && app.conn.open && app.state.shown) trSave = setTimeout(flushTranscript, SAVE_MS);
}
function flushTranscript(closing = false) {
  clearTimeout(trSave);
  trSave = null;
  // Not mid-turn: streamed entries exist only in the server's memory until the
  // turn ends, so a copy taken now could bring back half-written rows after a
  // restart. The turn's last patch arms the save again.
  if (app.state.sessions.get(app.state.shown)?.state === "working") return;
  if ((app.conn.open || closing) && app.state.shown && transcript.rev >= 0)
    saveTranscript(app.state.shown, { entries: [...transcript.entries.values()], rev: transcript.rev });
}
// Going hidden writes the Fleet even unchanged: its saved_at is when this
// device last saw the server, which is what "seen N min ago" says offline.
listen(document, "visibilitychange", () => {
  if (!document.hidden) return;
  flushFleet();
  if (trSave) flushTranscript();
});

// Offline, a session with nothing kept on this device says so.
function drawOfflineNotice() {
  $("tr-offline").hidden = live || !app.state.shown || transcript.entries.size > 0;
}

// Patches that only update sessions the page already shows redraw once a
// frame, however many arrive, and only those sessions' tab and card (#158).
let changed = new Set();
let setChanged = false;
let frame = 0;

function flushSessions() {
  frame = 0;
  const ids = changed;
  changed = new Set();
  if (!booted) return;
  app.state.ordered = app.state.ordered.map((m) => app.state.sessions.get(m.key)).filter(Boolean);
  header.refresh();
  header.size(); // its count includes quiet sessions, which have no tab
  // A session that went quiet or came back changes the set of tabs (#386).
  if (header.quietChanged()) {
    render();
    drawNav();
    return;
  }
  const r = route();
  let whole = false; // a grouped tab redraws the bar (tabs.js patchTab)
  for (const id of ids) if (app.state.sessions.has(id) && !header.patch(app.state.sessions.get(id), tabFocus(r))) whole = true;
  if (whole) header.render(tabbed(), tabFocus(r), headerView(r));
  if (r.view === "fleet") {
    fleet.patch(ids);
  } else if (r.view === "group" && fleet.groupKey === r.id) {
    fleet.patchGroup(ids);
  } else if (r.view === "session" && ids.has(r.id)) renderMeta(app.state.sessions.get(r.id));
  else if (r.view === "term" && ids.has(r.id)) terminal.draw(termCard(r.id));
  else if (r.view === "window" && ids.has(r.id)) drawWindow(r);
  // A browser shows in its tab and in canvas panes: every view redraws.
  for (const id of ids) if (app.state.sessions.get(id)?.kind === "browser") drawBrowser(termCard(id));
  drawNav(); // the needs count
}

function onSessions() {
  // A terminal's card has no created_at; it starts when it started.
  const since = (m) => m.created_at ?? m.started_at ?? 0;
  const ids = order.arrange(
    [...app.state.sessions.values()].sort((a, b) => since(a) - since(b)).map((m) => m.key),
    // The device's copy before this server's snapshot: it may not hold every
    // tab yet, so it must not narrow the order it was read from.
    homeLive,
  );
  app.state.ordered = ids.map((id) => app.state.sessions.get(id));
  header.refresh();
  header.size(); // its count includes quiet sessions, which have no tab
  render();
  drawNav(); // the needs count
  ping();
  keepFleet();
}

// Notifications follow live sessions only. Until this server's snapshot
// lands the saved Fleet is all there is, and a session that changed while
// the page was closed would ping on every cold start.
let homeLive = false;
function ping() {
  // The count in the title and on the favicon is the page's, not a host's.
  if (page && homeLive) updatePing([...app.state.sessions.values()].filter((m) => !m.stale && !isTool(m)), { onOpen: openSession });
}

// -- rendering -----------------------------------------------------------------
// A window tab draws one window of a session's canvas and never becomes
// another view: window 1's pages would run in it a second time.
const openSession = (id) => route().view === "window" || go(hashOf(id));
// A tab's hash: a terminal's view, or a session's.
const hashOf = (key) => ({ term: `#t=${key}`, browser: `#b=${key}` })[app.state.sessions.get(key)?.kind] || `#s=${key}`;
const tabFocus = (r) => (r.view === "session" || r.view === "term" || r.view === "browser" ? r.id : null);
// A group's page is the Fleet's: its button stays on while one shows.
const headerView = (r) => (r.view === "group" ? "fleet" : r.view);
// The open terminal's card, with the log id and server it is reached by.
function termCard(key) {
  const m = app.state.sessions.get(key);
  return m && { ...m, id: app.forKey(key).log_id };
}
// The service worker (sw.js): it keeps this build for offline use and raises
// notifications. A new build downloads beside this one; reloading takes it.
// The worker, push and notification taps belong to the page: a host has its
// own (m5). The in-page toasts below stay, because they are the mount's.
if (page) {
  startWorker({
    onUpdate: () =>
      toast("A new version of aegis is ready.", { ms: Infinity, action: { label: "Reload", run: applyUpdate } }),
  });
  setupPush((op, params) => app.conn.call(op, params));
  listenForOpen(openSession);
}
setToastOpener(openSession);
// Toasts from the server (App.toast): every browser shows each one once, and
// an agent's goes to the desktop too while the page is hidden. The snapshot is
// always empty, so a reconnect replays nothing.
channel(
  "toasts",
  () => {},
  (ops) => {
    for (const { toast: t } of ops) {
      if (!t) continue;
      toast(t.text, { kind: t.kind, from: t.from });
      // The desktop notification is the page's; a host draws the toast itself.
      if (page) pingToast(t, openSession);
    }
  },
);
// Close for the Fleet's Archive button and a middle click on a card or a tab:
// the session as it is now, not as the card or tab drew it.
const closeById = (id) => {
  const s = app.state.sessions.get(id);
  if (s) closeSession(s);
};
// This server's sessions, and a linked server's, in tab order.
const local = () => app.state.ordered.filter((m) => !m.server);

// Quiet sessions have no tab (#386). The view leaves a session only when it was
// seen visible and then turned quiet, so a link to a quiet one still opens it,
// even when the saved Fleet or a linked server's list arrives after the page.
const tabbed = () => app.state.ordered.filter((m) => !m.quiet);

// Archiving the open tab, here or elsewhere, leaves it the same way: to the next
// tab, not to the Fleet. The order is the one last drawn, which still holds it.
// Whether the list that would hold this key has arrived: a far session's
// server's, or this server's live one rather than the device's saved list.
function settled(key) {
  const far = app.forKey(key).server;
  return far ? app.state.linksLoaded && !(app.linkOf(far) && !app.state.remote.get(far)?.loaded) : homeLive;
}

function leaveIfGone(r) {
  const id = tabFocus(r);
  const was = [...header.quietSeen.keys()];
  const drawn = id && header.quietSeen.get(id) === false;
  const fresh = drawn && (app.state.sessions.get(id) ? r.view === "session" && app.state.sessions.get(id).quiet : settled(id));
  header.quietSeen.clear();
  for (const m of app.state.ordered) header.quietSeen.set(m.key, !!m.quiet);
  if (!fresh) return false;
  const at = was.indexOf(id);
  const next = was.slice(at + 1).concat(was.slice(0, at).reverse()).find((k) => header.quietSeen.get(k) === false);
  go(next ? hashOf(next) : "#fleet");
  return true;
}

// A click on a quiet card brings the session back to the tab bar, then opens it.
async function openCard(key) {
  // A locked node opens as it is, quiet: it gets its tab when the run ends.
  if (app.state.sessions.get(key)?.quiet && !locked(app.state.sessions.get(key))) {
    try {
      const back = await app.callFor(key, "session.unquiet");
      // Read again: a patch the server sent before the unquiet may have
      // replaced the meta meanwhile, still quiet, and the page would leave it.
      const m = app.state.sessions.get(key);
      if (m) Object.assign(m, back, { quiet: false, stay_quiet: false });
    } catch (e) {
      toast(e.message, { kind: "error" });
      return;
    }
    header.quietSeen.set(key, false);
  }
  openSession(key);
}
const on = (server) => app.state.ordered.filter((m) => m.server === server);

let menuView = null; // the view the open menu or bar belongs to (render)
function render() {
  if (!booted) return;
  // Back or Forward away from unsaved edits: stay on the file, and ask.
  if (root.dataset.view === "files" && filesView.leaving(route())) {
    const target = hashNow() || "#fleet";
    setHash(filesHash(filesView.path));
    filesView.confirmLeave().then((ok) => ok && go(target));
    return;
  }
  tellView(); // the view may have changed with the same session shown
  const r = route();
  // A row's link is read once: the URL drops "&at=" on every path, so the
  // tab's hash is its own again (cycle) and a quiet session's bounce keeps
  // none. The row waits in landEntry: a render that waits for the session
  // list runs again later with the plain hash.
  if (r.at) {
    setHash(`#s=${r.id}`);
    landEntry = { key: r.id, id: r.at };
  }
  // A fill is read once too, so a reload or Back never fills the form again.
  if (r.fill) {
    setHash("#new");
    spawn.show(r.fill);
  }
  // A menu or the selection bar is about the view it opened in. go() moves
  // with pushState, which fires no hashchange, so the change is caught here.
  const at = `${r.view}:${r.id ?? ""}`;
  if (at !== menuView) {
    menuView = at;
    cmenu.close();
    cmenu.unbar();
  }
  if (r.view !== "term") terminal.close();
  if (r.view !== "browser") browserTab.close();
  if (leaveIfGone(r)) return;
  // A session's link to a terminal, or the reverse, opens the right view.
  if ((r.view === "session" || r.view === "term" || r.view === "browser") && app.state.sessions.has(r.id) && `#${r.view[0]}=${r.id}` !== hashOf(r.id)) {
    setHash(hashOf(r.id));
    return render();
  }
  // A reload into a closed terminal: its row comes from term.closed.
  if (r.view === "term" && !app.state.sessions.has(r.id) && !fleet.closedTerm(r.id) && homeLive && !fleet.closedTermsLoaded) {
    fleet.loadClosedTerms().then(render);
    return;
  }
  if (r.view === "term" && !app.state.sessions.has(r.id) && fleet.closedTerm(r.id)) {
    const card = { ...fleet.closedTerm(r.id), state: "ended", reason: "closed", archived: true };
    header.render(tabbed(), null, "term");
    watchHost(null);
    follow(null);
    show("term");
    terminal.open(card.id, card, { conn: app.conn }).catch((e) => toast(e.message, { kind: "error" }));
    setTitle(`${card.name} · aegis`);
    return;
  }
  if ((r.view === "session" || r.view === "term") && !app.state.sessions.has(r.id)) {
    // A far session whose server's list has not arrived yet waits for it; a
    // home one waits for the live list, since a notification's tap or a link
    // opens a session the device's saved list may predate.
    if (!settled(r.id)) return;
    go("#fleet"); // a link to a session no tab held: gone before this page drew it
    return;
  }
  header.render(tabbed(), tabFocus(r), headerView(r));
  if (r.view !== "usage") usage.close();
  if (r.view !== "schedules") schedules.close();
  root.dataset.mode = r.view === "read" ? "read" : "live";
  // Quota rows redraw on a quota patch, the timer, or a change of view; never
  // on a sessions patch, which would take the hover tooltip with them.
  const viewKey = `${r.view}:${r.id || ""}`;
  const newView = viewKey !== quotaDrawnFor;
  quotaDrawnFor = viewKey;
  if (r.view === "fleet") {
    follow(null);
    show("fleet");
    fleet.render();
    watchHost("all");
    if (newView) drawQuota();
    setTitle("Fleet · aegis");
  } else if (r.view === "group") {
    // A group's page is the Fleet over its members only (#1039): the same
    // view, so a card opens its session and every key works as it does there.
    follow(null);
    show("fleet");
    watchHost(null);
    setTitle(`${fleet.renderGroup(r.id)} · aegis`);
  } else if (r.view === "spawn") {
    watchHost(null);
    follow(null);
    show("spawn");
    if (newView) spawn.loadHints();
    spawn.focus();
    setTitle("New session · aegis");
  } else if (r.view === "settings") {
    watchHost(null);
    follow(null);
    show("settings");
    if (newView) settings.open();
    setTitle("Settings · aegis");
  } else if (r.view === "journal") {
    watchHost(null);
    follow(null);
    show("journal");
    if (newView) journal.open();
    setTitle("Journal · aegis");
  } else if (r.view === "usage") {
    watchHost(null);
    follow(null);
    show("usage");
    if (newView) usage.open();
    setTitle("Usage · aegis");
  } else if (r.view === "knowledge") {
    watchHost(null);
    follow(null);
    show("knowledge");
    if (newView) knowledgeView.show(r.id);
    setTitle(`${r.id || "Knowledge"} · aegis`);
  } else if (r.view === "files") {
    watchHost(null);
    follow(null);
    show("files");
    filesView.show(r.id);
    const shown = parseTarget(r.id).path || filesView.path;
    setTitle(`${shown ? shown.split("/").pop() : "Files"} · aegis`);
  } else if (r.view === "schedules") {
    watchHost(null);
    follow(null);
    show("schedules");
    if (newView) schedules.open();
    setTitle("Schedules · aegis");
  } else if (r.view === "help") {
    watchHost(null);
    follow(null);
    show("help");
    if (newView) helpTab.show(r.id);
    setTitle("Help · aegis");
  } else if (r.view === "session") {
    // A row's link: the row is revealed now when its transcript is here, else
    // once it lands. The URL drops "&at=", so the tab's hash is its own again.
    // The session shown already: its transcript is here, so the row lands
    // now, or never when it is gone. Otherwise it lands with the transcript.
    // Only on the render that read the link: a later one may come before the
    // transcript lands, and must leave the row waiting for it.
    if (r.at && landEntry?.key === r.id && app.state.shown === r.id) {
      if (transcript.entries.has(landEntry.id)) transcript.reveal(landEntry.id);
      landEntry = null;
    }
    watchHost(app.forKey(r.id).server || "");
    // Shown first: follow() sizes the message box, which measures 0 while hidden.
    show("session");
    follow(r.id);
    renderMeta(app.state.sessions.get(r.id));
    if (newView) {
      drawQuota();
      sidebar?.drawHost();
    }
  } else if (r.view === "browser") {
    watchHost(null);
    follow(null);
    if (!app.state.sessions.has(r.id)) return homeLive && go("#");
    show("browser");
    const card = termCard(r.id);
    browserTab.open(card.id, card, { conn: app.conn, server: card.server ?? null });
    setTitle(`${card.name} · aegis`);
  } else if (r.view === "term") {
    watchHost(null);
    follow(null);
    show("term");
    const card = termCard(r.id);
    terminal.open(card.id, card, { conn: app.conn, server: card.server ?? null }).catch((e) => toast(e.message, { kind: "error" }));
    setTitle(`${card.name} · aegis`);
  } else if (r.view === "window") {
    // The session's card and transcript, as the session view has them (the
    // panes need the entries), with only window n drawn.
    watchHost(null);
    show("window");
    follow(app.state.sessions.has(r.id) ? r.id : null);
    drawWindow(r);
  } else {
    watchHost(app.forKey(r.id).server || "");
    show("session");
    follow(r.id);
    const m = fleet.findArchived(r.id);
    if (m) renderMeta({ ...m, state: "archived" });
    else if (!fleet.archiveLoaded) fleet.loadArchive().then(render);
    if (newView) drawQuota();
  }
}

// `want`: "all" for the Fleet, a server's name for a session ("" is this
// one), null for nothing.
function watchHost(want) {
  const wants = (name) => want === "all" || want === name;
  for (const [name, r] of app.state.remote) {
    if (wants(name) && !r.unsubHost)
      r.unsubHost = channel(
        "host",
        (snap) => {
          r.host = snap;
          drawHost();
        },
        (ops) => {
          for (const op of ops) if ("set" in op) r.host = op.set;
          drawHost();
        },
        undefined,
        undefined,
        name,
      );
    else if (!wants(name) && r.unsubHost) {
      r.unsubHost();
      r.unsubHost = null;
      r.host = null;
    }
  }
  if (wants("") && !app.state.unsubHost) {
    app.state.unsubHost = channel(
      "host",
      (snap) => {
        app.state.host = snap;
        drawHost();
      },
      (ops) => {
        for (const op of ops) if ("set" in op) app.state.host = op.set;
        drawHost();
      },
    );
  } else if (!wants("") && app.state.unsubHost) {
    app.state.unsubHost();
    app.state.unsubHost = null;
    app.state.host = null;
  }
}

function drawHost() {
  if (root.dataset.view === "fleet") fleet.band();
  else if (root.dataset.view === "session") sidebar?.drawHost();
}

// A speed reading now, on the server the row belongs to; the host channel
// brings the result, "measuring…" first.
function measureNet(server) {
  app.conn.call("net.measure", {}, server).catch((e) => toast(e.message, { kind: "error" }));
}

function drawQuota() {
  if (root.dataset.view === "fleet") {
    fleet.quota();
  } else if (root.dataset.view === "session") {
    sidebar?.drawQuota();
  }
}

// Countdowns and the tick move with the clock; their unit is minutes.
every(30 * 1000, drawQuota);

// The last TAB_CACHE tabs left, newest last: a return to one shows it at once
// and asks only for what changed since (transcript/wire.py, Fold.snapshot).
const TAB_CACHE = 8;

let follows = 0; // which follow() a device read answers

// The worker shows a push unless a window shows its session in front of the
// person (sw.js), so it hears what this one shows whenever that may change.
// A window tab shows a session's panes, not its transcript: it quiets no push,
// and a notification's click never opens a session in it.
function tellView() {
  // The worker's view message is the page's: a host's worker must hear nothing
  // from this mount (m5). Watching the session for parking stays: it is the
  // server's, not the page's.
  if (page) {
    const win = route().view === "window";
    navigator.serviceWorker?.controller?.postMessage({
      t: "view",
      key: win ? "" : app.state.shown || "",
      visible: !document.hidden && document.hasFocus(),
      window: win,
    });
  }
  tellWatch();
}
// Which session this page shows while it is visible, for parking (park.py): a
// session someone looks at keeps its harness. A hidden page counts as away.
let watchedServer = null;
function tellWatch() {
  const key = route().view === "session" ? app.state.shown || "" : ""; // the Fleet and other views watch nothing
  const { server, log_id } = key ? app.forKey(key) : { server: null, log_id: null };
  if (watchedServer !== null && watchedServer !== (server || "")) {
    const off = { t: "watch", session: null, visible: false };
    if (watchedServer) off.server = watchedServer;
    app.conn.post(off);
  }
  watchedServer = server || "";
  const msg = { t: "watch", session: log_id, visible: !!log_id && !document.hidden };
  if (server) msg.server = server;
  app.conn.post(msg);
}
listen(window, "hashchange", tellWatch);
for (const ev of ["focus", "blur"]) listen(window, ev, tellView);
listen(document, "visibilitychange", tellView);
// A worker that took this page over, on a first visit or after an update, has
// heard nothing from it yet. A host's worker is not ours to greet (m5).
if (page && navigator.serviceWorker) listen(navigator.serviceWorker, "controllerchange", tellView);

// What a full mount's host hears for one row: entry always, and fileSent for
// a file card (spec 06). A full snapshot is history, so entry replays and
// fileSent does not; a row already announced is never announced twice (m6).
const sentFiles = new Set();
function report(session, entry, live = false) {
  app.emit("entry", { session, entry });
  if (entry.kind !== "file") return;
  const id = entry.id;
  if (!live) return void (id != null && sentFiles.add(id));
  if (id != null) {
    if (sentFiles.has(id)) return;
    sentFiles.add(id);
  }
  app.emit("fileSent", { session, entry, files: entryFiles(entry) });
}

// The router's first follow is the constructor's own session: the host passed
// it, so it hears no sessionChanged for it (m6). A full mount that started on
// the fleet has no such first follow, and its later open() reports previous
// null as chat does.
let firstFollow = true;
function follow(id) {
  if (app.state.shown === id) return;
  const previous = app.state.shown;
  const quiet = firstFollow && id === app.opts.session;
  firstFollow = false;
  if (trSave) flushTranscript(); // before its rows go
  closeSide();
  if (unsubTranscript) unsubTranscript();
  unsubTranscript = null;
  if (app.state.shown) {
    app.state.kept.delete(app.state.shown);
    app.state.kept.set(app.state.shown, transcript.stash());
    while (app.state.kept.size > TAB_CACHE) app.state.kept.delete(app.state.kept.keys().next().value);
  } else transcript.clear();
  drawNavNow(); // at once: the old session's navigator goes with its rows
  find.close(false); // it searched the old session
  dictation.stopIfHere(input, "tab"); // a recording belongs to its session
  speaker.stop(); // what is being read belongs to the session on screen
  app.state.shown = id;
  // The host hears where the view moved, with the row as the tab sees it and
  // null before the server's list names it (m6).
  if (!quiet && previous !== id)
    app.emit("sessionChanged", {
      session: id,
      previous,
      meta: (id && app.state.sessions.get(id)) || null,
    });
  drawSpeak();
  if (app.state.sessions.has(id)) sessionStorage.setItem("aegis.lastShown", (lastShown = id));
  tellView();
  const turn = ++follows;
  const first = early;
  early = null; // read once: a later visit reads what the device holds then
  atts.show(id);
  drawQueued();
  if (!id) {
    canvas.clear();
    return viewer.show(null);
  }
  // The base before any row is drawn: a kept tab's rows mount at once, and
  // would take the last tab's server's base (#425).
  const where = app.forKey(id);
  setFileBase(where.server ? `/via/${where.server}` : "");
  const saved = app.state.kept.get(id);
  if (saved) {
    app.state.kept.delete(id);
    transcript.restore(saved);
  }
  // The divider is placed by the first snapshot or delta after this switch,
  // and kept across a resubscribe of the same session.
  let placed = false;
  viewer.show(id); // after the base: a far artifact's frame is built from it
  if (app.state.sessions.get(id)) showCanvas(app.state.sessions.get(id));
  else canvas.clear();
  const subscribe = () => {
    unsubTranscript = app.conn.subscribe(
      `transcript:${where.log_id}`,
      (data) => {
        // A server that cannot resume from the revision asked (the session was
        // reset, its store rewritten) answers in full, and the full answer
        // replaces whatever was drawn.
        if (data.since !== undefined) {
          transcript.resume(data);
          for (const entry of data.entries || []) report(id, entry, true);
        } else {
          transcript.snapshot(data);
          // History: entry replays for the host, fileSent does not.
          for (const entry of data.entries || []) report(id, entry);
        }
        canvas.refresh();
        keepTranscript();
        drawOfflineNotice();
        if (!placed) {
          transcript.setSince(sinceText(app.state.sessions.get(id)));
          askRecap(false); // the server decides whether it is worth one
          if (landUnread === id) transcript.firstUnread();
          landUnread = null;
          if (landEntry && landEntry.key === id) transcript.reveal(landEntry.id);
          landEntry = null;
        }
        placed = true;
        // Read by scripts/bench.py: when the snapshot was drawn and painted.
        const mark = (window.__a2snapshot = { at: performance.now(), count: transcript.entries.size });
        requestAnimationFrame(() => (mark.painted = performance.now()));
      },
      (ops) => {
        for (const op of ops) if (op.probe) artifacts.probe(op.probe, id, fileUrl(op.probe.url));
        // A new agent message while another tab is open dots the transcript's.
        const fresh = ops.some((op) => op.upsert?.unread && !transcript.entries.has(op.upsert.id));
        transcript.apply(ops);
        if (fresh) canvas.mark("transcript");
        for (const op of ops) if (op.upsert) canvas.onEntry(op.upsert);
        for (const op of ops) if (op.upsert) report(id, op.upsert, true);
        keepTranscript();
      },
      undefined,
      // Holding nothing, a full snapshot mounts only the last rows; a delta
      // from -1 would mount every row through apply().
      () => (transcript.rev >= 0 ? transcript.rev : null),
      where.server,
    );
  };
  if (saved) {
    subscribe();
    drawOfflineNotice();
  } else
    // What this device kept of the session, drawn at once. The subscription
    // waits for it (a local read, a few ms) so that it asks only for what
    // changed since its revision.
    (first?.id === id ? first.copy : loadTranscript(id)).then((copy) => {
      if (turn !== follows) return; // moved on
      if (copy) transcript.snapshot({ entries: copy.entries, rev: copy.rev });
      canvas.refresh();
      drawOfflineNotice();
      subscribe();
    });
  menu.close();
  $("input").value = localStorage.getItem(`aegis.draft.${id}`) || "";
  autosize();
  drawSending();
}

// The divider's label: how long since this session was last read.
function sinceText(s) {
  const t = s?.last_read_at;
  if (!t) return "new since you left";
  const m = Math.round((serverNow() - t) / 60);
  return `new since you left · ${m < 60 ? `${m} min` : `${Math.round(m / 60)} h`}`;
}

function fmtTokens(n) {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

// A window tab: its panes, and the session's name in its title so the
// browser's tab list tells the windows apart. A session gone from the list
// leaves the window empty.
function drawWindow(r) {
  const s = app.state.sessions.get(r.id);
  if (s) showCanvas(s);
  else canvas.clear();
  setTitle(`${s ? s.title || s.handle : "aegis"} · window ${r.n}`);
}

function renderMeta(s) {
  if (!s) return;
  // The panel draws its own card, plan, repos, journal, usage, quota, host,
  // monitors and actions; the page draws the transcript, the composer and the
  // title around them.
  sidebar?.render(s);
  if (s.key === app.state.shown) showCanvas(s);
  drawReplies(s);
  drawCold(s);
  drawClosing(s);
  $("chip-model").replaceChildren(factIcon("h", s.harness), s.model);
  $("chip-model").title = s.model_id || "";
  $("chip-effort").replaceChildren(factIcon("e", s.effort), `${s.effort} effort`);
  $("chip-perm").replaceChildren(factIcon("p", s.permission), s.permission);
  const working = s.state === "working";
  $("interrupt").hidden = !working;
  // A workflow's node is the engine's while its run lives, and a handed-off
  // session its successor's (#874).
  const held = locked(s) || !!s.continued_by;
  $("composer").hidden = !!s.continued_by;
  $("working").hidden = !working;
  $("interrupt").disabled = held;
  if (working && workingSince == null) {
    workingSince = Date.now();
    workingShown = [0, 0];
  }
  workingTokens = s.turn_tokens || [0, 0];
  if (!working) workingSince = null;
  // A linked server that is down: the transcript stays readable, nothing sends.
  const down = s.off ? app.linkOf(s.server) : null;
  input.disabled = $("attach").disabled = !!down || held;
  drawSending(!!down || held);
  if (s.asleep_why !== "idle" && warmed === app.state.shown) warmed = null;
  drawComposerHint(s);
  setTitle(`${s.title || s.handle} · aegis`);
}

// The composer's placeholder: whose session, whether its server is reachable,
// and whether a message resumes it. While a parked session's harness resumes
// after the first keystroke, the waking row says so in the transcript instead.
function drawComposerHint(s) {
  $("waking").hidden = !(s.asleep_why === "idle" && warmed === app.state.shown);
  const down = s.off ? app.linkOf(s.server) : null;
  // Chrome counts a placeholder in scrollHeight: measure again when it changes.
  const placeholder = locked(s)
    ? `Step ${s.workflow.node} of the workflow ${workflowTitle(s.workflow.run) || s.workflow.run}. You can read it; it takes no messages until the run stops.`
    : down
    ? `${s.server} is ${down.state} since ${new Date(down.since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}; nothing can be sent until it is back.`
    : s.state === "asleep" && s.asleep_why === "idle"
      ? idleFor(s)
        ? `Asleep after ${idleFor(s)} idle. Typing wakes it.`
        : "Asleep while idle. Typing wakes it."
      : s.state === "asleep"
      ? "Asleep; your next message wakes it."
      : touch.matches
        ? ""
        : "Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts.";
  if (input.placeholder !== placeholder) {
    input.placeholder = placeholder;
    autosize();
  }
}

// The working row (#938): a verb types out, the cursor blinks on it, it
// backspaces and the next one types; the counters ease toward the turn's
// tokens and flash when a call adds to them. Reduced motion keeps one verb.
const VERBS = ["Working", "Thinking", "Weaving", "Brewing", "Forging", "Pondering", "Tinkering", "Conjuring", "Untangling", "Crafting"];
const stillMotion = matchMedia("(prefers-reduced-motion: reduce)");
const typer = { verb: 0, at: 0, phase: "type", wait: 0 };
function drawVerb(text) {
  $("working-verb").replaceChildren(
    ...[...text].map((c, i) => {
      const ch = span("", c);
      ch.style.animationDelay = `${i * 0.07}s`;
      return ch;
    }),
  );
}
function tokenCount(n) {
  return n < 1000 ? String(Math.round(n)) : `${(n / 1000).toFixed(1)}k`;
}
const callTitle = new CallTitle();
function tickWorking() {
  if (workingSince == null) return;
  $("working-s").textContent = Math.round((Date.now() - workingSince) / 1000);
  ["working-in", "working-out"].forEach((id, k) => {
    const gap = workingTokens[k] - workingShown[k];
    if (gap > workingShown[k] * 0.02 + 1 && !$(id).classList.contains("flash")) {
      $(id).classList.add("flash");
      setTimeout(() => $(id).classList.remove("flash"), 350);
    }
    workingShown[k] = Math.abs(gap) < 1 ? workingTokens[k] : workingShown[k] + gap * 0.15;
    $(id).textContent = tokenCount(workingShown[k]);
  });
  if (callTitle.draw($("entries"), $("working"), app.t, stillMotion.matches)) return;
  const word = `${VERBS[typer.verb]}…`;
  if (stillMotion.matches) {
    if ($("working-verb").textContent !== "Working…") drawVerb("Working…");
    return;
  }
  if (typer.wait > 0) typer.wait -= 1;
  else if (typer.phase === "type") {
    typer.at += 1;
    if (typer.at >= word.length) Object.assign(typer, { phase: "erase", wait: 50 });
  } else if (typer.at > 0) typer.at -= 1;
  else Object.assign(typer, { phase: "type", verb: (typer.verb + 1) % VERBS.length, wait: 4 });
  const text = `${VERBS[typer.verb]}…`.slice(0, typer.at);
  if ($("working-verb").textContent !== text) drawVerb(text);
}
every(60, tickWorking);

every(1000, () => {
  tickMonitors();
  tickFeeds();
  fleet.tick();
  if (root.dataset.view === "session" && app.state.shown) {
    const s = app.state.sessions.get(app.state.shown);
    sidebar?.tickPlan(s);
    drawCold(s);
  }
});

function span(cls, text, name) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  if (name) el.prepend(icon(name));
  return el;
}

// -- the recap -----------------------------------------------------------------
// Asked on landing and by the sparkle or the row's refresh. The entry arrives on
// the transcript channel; the answer only says why there is none, and only to a
// person who asked: the request on landing never writes a hint.
async function askRecap(force) {
  const id = app.state.shown;
  if (!id) return;
  let why = "";
  let kind = "error";
  try {
    const r = await app.callFor(id, "recap.request", { force });
    if (r.status === "off" || r.status === "failed") why = r.why;
    else if (r.status === "busy") {
      why = "the agent is still working; ask again when it is done";
      kind = "info";
    }
  } catch (e) {
    why = e.message;
  }
  if (force && why && app.state.shown === id) toast(why, { kind });
}
$("entries").addEventListener("click", (ev) => {
  const cancel = ev.target.closest("[data-btw-cancel]");
  if (cancel && app.state.shown) {
    app.callFor(app.state.shown, "btw.cancel", { id: cancel.dataset.btwCancel }).catch((e) => toast(e.message, { kind: "error" }));
    return;
  }
  const btwRow = ev.target.closest(".row.btw");
  if (btwRow && (ev.target.closest(".hd") || btwRow.classList.contains("folded"))) {
    const folded = !btwRow.classList.contains("folded");
    btwFolds.set(btwRow.dataset.btw, folded);
    btwRow.classList.toggle("folded", folded);
  }
});
// Coming back to the page is landing again; the server decides.
listen(document, "visibilitychange", () => {
  if (document.visibilityState === "visible" && app.state.shown) askRecap(false);
});

// -- Open natively on a sent file's card, or in the viewer's bar --------------
root.querySelector(".v-session .col").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".fbar .native");
  if (!b) return;
  b.disabled = true;
  try {
    await app.conn.call("file.open", { file_id: b.dataset.fileId, name: b.dataset.name });
  } catch (e) {
    toast(e.message, { kind: "error" });
  } finally {
    setTimeout(() => (b.disabled = false), 800);
  }
});

// Show on a finished artifact's card: the frame again, read-only.
$("entries").addEventListener("click", (ev) => {
  const b = ev.target.closest(".acard .show");
  if (!b) return;
  const rowEl = b.closest(".row");
  const e = transcript.entries.get(rowEl.dataset.id);
  const card = rowEl.querySelector(".acard");
  const old = card.querySelector(".stage");
  if (old) old.remove();
  else card.append(artifactStage(e));
});

// -- Show the file a Read, Write or Edit row used -------------------------------
$("entries").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".peekbar .peek");
  if (!b) return;
  b.disabled = true;
  try {
    // The row comes back with the card in it; this node is replaced. Only
    // the answer says where the file sits under the root (viewer.js).
    // A linked server's row asks that server, which copies its own file (#1044).
    const { log_id, server } = app.forKey(app.state.shown);
    const files = await app.conn.call("file.peek", { log_id, entry_id: b.dataset.entry }, server);
    if (!server) for (const f of files) if (f.root_path) peekRoots.set(f.url, f.root_path);
  } catch (e) {
    b.nextElementSibling.textContent = e.message;
    b.disabled = false;
  }
});

// -- Open a path a reply named, in the viewer at its line -----------------------
$("entries").addEventListener("click", async (ev) => {
  const code = ev.target.closest(".md code.ref:not([data-kind=dir])");
  if (!code) return;
  const row = code.closest(".row");
  const body = row.querySelector(":scope > .body");
  const line = Number(/:(\d+)$/.exec(code.textContent.trim())?.[1]) || null;
  const open = async (pick) => {
    body.querySelector(":scope > .patherr")?.remove();
    try {
      const files = await app.conn.call("file.peek", { log_id: app.forKey(app.state.shown).log_id, entry_id: row.dataset.id, index: Number(code.dataset.index), pick });
      viewer.open({ files, at: 0, remote: false, glyph: "file", line });
    } catch (e) {
      const note = document.createElement("div");
      note.className = "patherr";
      note.textContent = e.message;
      // A path under both the config root and the session's folder: the person picks.
      if (e.code === "ambiguous_path") {
        const picks = document.createElement("div");
        for (const [pick, text] of [["root", "Open the root's"], ["cwd", "Open the session folder's"]]) {
          const b = document.createElement("button");
          b.className = "btn";
          b.textContent = text;
          b.addEventListener("click", () => open(pick));
          picks.append(b);
        }
        note.append(picks);
      }
      body.append(note);
    }
  };
  await open();
});

async function reopen(id) {
  try {
    await app.callFor(id, "session.reopen");
    fleet.invalidateArchive();
    go(`#s=${id}`);
  } catch (e) {
    sidebar?.fail(e.message);
  }
}


// A harness version outside the tested range (harness_versions.py): the
// server refused the launch once with what it found; ask the person whether to
// start once, allow that version for good, or cancel. Resolves "once",
// "always" or null.
function untestedAsk(d) {
  const p = (cls, text) => {
    const e = document.createElement("p");
    e.className = cls;
    e.textContent = text;
    return e;
  };
  const body = document.createElement("div");
  body.className = "untested";
  const range = d.min === d.max ? d.max : `${d.min} to ${d.max}`;
  const head = d.state === "unknown" ? `${d.label}: version unknown` : `${d.label} ${d.version}`;
  body.append(p("range", `${head} · tested with ${range}`));
  body.append(p("text", (d.message || "").split("\n")[0]));
  body.append(p("found", `found: ${d.path || "nothing on PATH"}`));
  const cmd = document.createElement("div");
  cmd.className = "cmd";
  const code = document.createElement("code");
  code.textContent = d.install;
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "btn copy-cmd";
  copy.textContent = "Copy";
  copy.addEventListener("click", () =>
    navigator.clipboard.writeText(d.install).then(
      () => toast("Copied"),
      () => toast("Could not copy", { kind: "error" }),
    ),
  );
  cmd.append(code, copy);
  body.append(cmd);
  for (const n of d.notes || []) body.append(p("note", n));
  const buttons = [{ label: "Cancel", value: null }];
  if (d.may_always) buttons.push({ label: `Always allow ${d.version}`, value: "always" });
  buttons.push({ label: "Start once", value: "once", primary: true });
  const title = d.state === "unknown" ? `${d.label}'s version is unknown` : `${d.label} ${d.version} is untested`;
  return choose(title, body, buttons);
}

// A call that starts a harness, asked again with the person's answer when the
// server refuses it as harness_untested. Cancel resolves CANCELLED, which no
// answer can be: session.send answers a delivery with nothing at all.
const CANCELLED = Symbol("cancelled");
async function untestedRetry(call, again) {
  try {
    return await call();
  } catch (e) {
    if (e.code !== "harness_untested" || !e.data) throw e;
    const pick = await untestedAsk(e.data);
    if (!pick) return CANCELLED;
    const r = await again(pick);
    if (r?.not_saved) toast(`Started once: ${r.not_saved}`, { kind: "error" });
    return r;
  }
}

// -- keys -----------------------------------------------------------------------
// The ? list: drawn once from the key table.
const keymap = views.keymap;
renderKeys(keymap);
const help = (open = keymap.hidden) => (keymap.hidden = !open);
keymap.addEventListener("click", (ev) => ev.target === keymap && help(false));
const palette = new Palette(views.palette, () => route().view);

// Alt+J and the navigator's button. In byNeed's order, from the tab after this
// one; from the top when this one is not in the list, as after reading a
// review, which drops it.
function nextNeed() {
  const list = byNeed(app.state.ordered);
  if (!list.length) return toast("Nobody needs you");
  const r = route();
  const i = r.view === "session" ? list.findIndex((m) => m.key === r.id) : -1;
  const id = list[(i + 1) % list.length].key;
  if (list[i]?.key === id) return transcript.firstUnread(); // the only one, and open
  landUnread = id;
  go(`#s=${id}`);
}

// What each key in keys.js does. `input` is declared below;
// a key is pressed only after this module has run.
// A transcript key does nothing while the canvas's tabs hide the transcript.
const onTranscript = (run) => (ev) => canvas.transcriptShown() && run(ev);
const offKeys = installKeys(
  {
    composer() {
      const v = route().view;
      if (v === "session") input.focus();
      // The new tab's message box: Enter there spawns and sends.
      else if (v === "spawn") spawn.focus();
    },
    dictate() {
      if (offline()) return;
      const v = route().view;
      if (v === "spawn") spawn.dictate();
      else if (v === "session" && navigator.mediaDevices) dictation.toggle(input, "key");
    },
    browse() {
      const v = route().view;
      if (v === "session" || v === "read") {
        canvas.showTranscript();
        $("tr").focus({ preventScroll: true });
        transcript.pick();
      } else if (v === "fleet") {
        fleet.focus();
      }
    },
    needs: nextNeed,
    tabPrev: () => header.cycle(-1),
    tabNext: () => header.cycle(1),
    tabLeft: () => header.stepTab(-1),
    tabRight: () => header.stepTab(1),
    next: onTranscript(() => transcript.move(1)),
    prev: onTranscript(() => transcript.move(-1)),
    turnNext: onTranscript(() => transcript.moveTurn(1)),
    turnPrev: onTranscript(() => transcript.moveTurn(-1)),
    first: onTranscript(() => transcript.edge(false)),
    last: onTranscript(() => transcript.edge(true)),
    messagePrev: onTranscript(() => transcript.message(-1)),
    messageNext: onTranscript(() => transcript.message(1)),
    firstUnread: onTranscript(() => transcript.firstUnread()),
    toggle: onTranscript(() => transcript.toggle()),
    find() {
      canvas.showTranscript(); // the bar is the transcript's
      find.open();
    },
    press: onTranscript(() => transcript.press()),
    copy: onTranscript(() => transcript.selected && copyRow(transcript.selected, transcript.nodes.get(transcript.selected), copyDeps)),
    foldLevel: () => foldLevel((transcript.foldLevel + 1) % 3),
    walk() {},
    fleetNext: () => fleet.move(1),
    fleetPrev: () => fleet.move(-1),
    fleetOpen: () => fleet.openSel(),
    filter: () => fleet.filter(),
    // The focused row's menu, else the selected transcript row's or Fleet card's.
    menuKey() {
      const at = document.activeElement?.closest?.("[data-id], .trow");
      if (cmenu.openFor(at)) return;
      const v = route().view;
      if (v === "session" || v === "read") cmenu.openFor(transcript.selected && transcript.nodes.get(transcript.selected));
      else if (v === "fleet") cmenu.openFor(fleet.selected());
    },
    spawn: () => spawn.open(),
    settings: () => go("#settings"),
    journal: () => go("#journal"),
    usage: () => go("#usage"),
    knowledge: () => go("#knowledge"),
    files: () => filePicker.open(),
    save: () => route().view === "files" && filesView.save(),
    schedules: () => go("#schedules"),
    tab(ev) {
      const n = Number(ev.altKey ? ev.code.slice(5) : ev.key);
      if (n === 0) go("#fleet");
      else if (tabbed()[n - 1]) go(hashOf(tabbed()[n - 1].key));
    },
    workflow() {
      if (route().view !== "session") return;
      menu.setLine("/workflow ");
      input.focus();
      menu.openInline();
    },
    commands() {
      if (route().view !== "session") return;
      const v = input.value;
      if (!v || v.startsWith("/")) {
        if (!v) input.value = "/";
        input.focus();
        menu.openInline();
      } else menu.openOverlay();
    },
    escape() {
      if (dictation.discard() || cancelAsk() || closeInstallDialog() || closePushPanel()) return;
      if (header.close()) return;
      if (root.dataset.side === "open") return closeSide();
      if (!keymap.hidden) help(false);
      else if (cmenu.close() || cmenu.unbar() || viewer.close() || find.close() || closeMonitorCard() || closeFeedCard() || sidebar?.closeCard()) return;
      else if (route().view === "session" && !sidebar?.isEditing()) interrupt();
    },
    help: () => help(),
    side: () => toggleSide(),
    archive() {
      if (route().view === "session") sidebar?.archive();
    },
    quiet() {
      if (route().view === "session") sidebar?.quietSession();
    },
    palette: () => palette.toggle(),
    sessions: () => {
      palette.close();
      header.sessions();
    },
  },
  () => (booted ? route().view : "boot"),
);
teardown.push(offKeys);

// -- composer ---------------------------------------------------------------
const input = $("input");

// A composer send gets an answer within SEND_VERDICT_MS (#350). Until then the
// box and the button are locked. Plain text with no answer by then becomes the
// session's queued draft and the socket is dropped as stalled (#429); a command
// or files go back in the box, since only text waits on the device.
const SEND_VERDICT_MS = 5000;
const LATE = new Error("no answer yet");
const NOT_CONFIRMED = "No answer in 5 s. Send again: it will not arrive twice.";
const answered = (call) =>
  Promise.race([call, new Promise((_, reject) => setTimeout(() => reject(LATE), SEND_VERDICT_MS))]);
const sending = new Set(); // sessions whose composer send waits on its answer
// The nonce of the last send per session that got no answer yet: sending the
// same message again reuses it, and the server delivers it once.
const unconfirmed = new Map(); // key -> {what, nonce}
function nonceFor(key, what) {
  const u = unconfirmed.get(key);
  if (u?.what === what) return u.nonce;
  const nonce = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  unconfirmed.set(key, { what, nonce });
  return nonce;
}
// The lock on the shown session's box. `blocked` is renderMeta's own reason to
// disable the button (a linked server down, someone else's session).
let blocked = false;
function drawSending(why = blocked) {
  blocked = why;
  const on = !!app.state.shown && sending.has(app.state.shown);
  $("composer").classList.toggle("sending", on);
  input.readOnly = on;
  $("send").disabled = on || blocked;
}

// -- attachments: chips above the box, uploading as they arrive (attach.js) --
const waitOnline = () =>
  new Promise((resolve) => {
    const t = setInterval(() => {
      if (app.conn.open) {
        clearInterval(t);
        resolve();
      }
    }, 500);
  });
const atts = new Attachments($("atts"), { call: (key, op, params) => app.callFor(key, op, params), waitOnline });
$("attach").addEventListener("click", () => !offline() && $("attach-pick").click());
$("attach-pick").addEventListener("change", (ev) => {
  atts.add([...ev.target.files]);
  ev.target.value = "";
});
// A pasted screenshot is called image.png by every browser: name it by time.
function pasted(ev) {
  const got = [...(ev.clipboardData?.files || [])];
  if (!got.length) return null;
  ev.preventDefault();
  const at = new Date().toTimeString().slice(0, 8).replaceAll(":", "");
  return got.map((f, i) => {
    const ext = (f.type.split("/")[1] || "bin").replace(/[^a-z0-9]/g, "");
    return new File([f], `pasted-${at}${i ? `-${i + 1}` : ""}.${ext}`, { type: f.type });
  });
}
input.addEventListener("paste", (ev) => {
  const files = pasted(ev);
  if (files) atts.add(files);
});
// A file dropped anywhere on a session's page attaches to it; anywhere else
// the browser would navigate away to the file.
const carriesFiles = (ev) => ev.dataTransfer?.types.includes("Files");
const onSession = () => root.dataset.view === "session";
// A file dropped on the host's page is the host's: only a drop inside this
// mount's root attaches to its session (m5).
listen(document, "dragover", (ev) => {
  if (!root.contains(ev.target)) return;
  if (!carriesFiles(ev)) return;
  ev.preventDefault();
  $("composer").classList.toggle("dropping", onSession());
});
listen(document, "dragleave", (ev) => {
  if (!root.contains(ev.target)) return;
  if (ev.relatedTarget) return;
  $("composer").classList.remove("dropping");
});
listen(document, "drop", (ev) => {
  if (!root.contains(ev.target)) return;
  if (!carriesFiles(ev)) return;
  ev.preventDefault();
  $("composer").classList.remove("dropping");
  if (onSession()) atts.add([...ev.dataTransfer.files]);
});

// -- dictation: the session's message box (parts/dictation.js) ---------------
// The page's one dictation part. The session's box owns it and builds the mic
// index.html used to hold; the new tab's first-message box joins it from the
// spawn part.
// The drafts that got dictated text since their last send, by draft key:
// their send tells the server, which adds a note for the agent (#579).
const spoken = new Set();
const onTake = (file, ms, ctx) => {
  // A take that ends after its tab was left waits in that session's tray
  // rather than going out unseen (record.js, #932).
  if (!ctx?.key) return;
  atts.add([file], ctx.key);
  if (app.state.shown === ctx.key && onSession()) send();
  else toast("The recording waits in its session's box: send it from there");
};
const dictation = new DictationPart(app, $("composer"), {
  input: () => input,
  // A session's box holds that session's draft, so text that lands after the
  // box moved to another session goes to the draft it started in.
  draft: () => (app.state.shown ? `aegis.draft.${app.state.shown}` : null),
  heard: (key) => key && spoken.add(key),
  ctx: () => ({ key: app.state.shown }),
  canStart: () => !offline(),
  prepare: () => app.conn.call("dictation.prepare"),
  onState: (state) => artifacts.dictationState(state),
  onLevel: () => artifacts.dictationCheck(),
  onTake,
});
// On a touch screen Enter adds a line and the button sends: the key sits where
// a mistap lands, and half a message costs a turn.
const touch = matchMedia("(pointer: coarse)");

// The New session view is a part (b5): it builds the markup index.html used to
// hold and finds every node under it. The page hands it what only it knows —
// navigation, the session's first send, the untested-harness question, the
// agents of a server for its menu, and the page's dictation engine.
const spawn = new SpawnPart(app, root, {
  go,
  untestedRetry,
  cancelled: CANCELLED,
  sendFirst,
  sendLine,
  stackActs,
  pasted,
  agents: spawnAgents,
  dictation,
});

// The buttons sit beside one line of text and drop under a longer one, so its
// lines get the box's whole width (#575). It measures at the narrow width every
// time: measured wide, a text that fits there would flip back and forth. A
// placeholder never stacks: an empty box keeps its height. On a touch screen a
// focused box stacks at once, so the first line typed has the whole width
// (#707). The measure is a hidden copy: resizing the box itself to measure lays
// out the whole transcript again, seconds for a long message.
const ruler = document.createElement("textarea");
ruler.ariaHidden = "true";
ruler.tabIndex = -1;
ruler.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;height:0;padding-top:0;padding-bottom:0;border:0;overflow:hidden;box-sizing:border-box";
document.body.append(ruler);
function stackActs(t) {
  const box = t.closest(".box");
  if (!box.clientWidth) return box.classList.remove("stacked");
  if (touch.matches && document.activeElement === t) return box.classList.add("stacked");
  if (!t.value) return box.classList.remove("stacked");
  const css = getComputedStyle(t);
  for (const k of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingLeft", "paddingRight"]) ruler.style[k] = css[k];
  ruler.style.width = `${box.clientWidth - box.querySelector(".acts").offsetWidth}px`;
  ruler.value = t.value;
  box.classList.toggle("stacked", ruler.scrollHeight > parseFloat(css.lineHeight) * 1.5);
}

function autosize() {
  stackActs(input);
  input.style.height = "auto";
  const room = window.innerHeight * 0.4;
  input.style.height = `${Math.min(input.scrollHeight, room)}px`;
  // A scroll bar only once the text outgrows the room (#707).
  input.style.overflowY = input.scrollHeight > room ? "auto" : "hidden";
}
input.addEventListener("focus", autosize);
input.addEventListener("blur", autosize);
// A tap on the box's buttons leaves the focus in the text: a blur would unstack
// the box and move the button out from under the finger (#707).
$("composer").querySelector(".acts").addEventListener("mousedown", (ev) => touch.matches && ev.preventDefault());
// A pane docked beside the transcript, or a button appearing beside the text,
// narrows the message box: its lines wrap again, so it measures again. Width
// only, so its own height is no loop.
let composerWidth = 0;
observe(
  new ResizeObserver(([e]) => {
    if (Math.round(e.contentRect.width) === composerWidth) return;
    composerWidth = Math.round(e.contentRect.width);
    autosize();
  }),
  input,
);

function focused() {
  const r = route();
  return r.view === "session" ? app.state.sessions.get(r.id) : null;
}

// The agent's suggested next messages, from its turn_end. Redrawn only when they
// change: renderMeta runs on every patch of the open session. They sit after the
// transcript's last row, not in the composer, so they scroll away with it and
// never take reading space (#286).
// The prompt cache is cold once the session has sat idle past its harness's
// measured lifetime; the card names the thresholds, or null (cache.py).
function drawCold(s) {
  const box = $("coldnote");
  const c = s && s.cache_cold;
  const on = !!c && s.state !== "working" && !s.continued_by && (s.context_tokens ?? 0) >= c.min && nowS() - (s.last_activity || nowS()) >= c.after;
  const key = on ? `${s.log_id}:${s.context_tokens}` : "";
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !on;
  // A queue worker's task owns it, so it is never handed off (#354).
  $("coldnote-go").hidden = !!s.worker;
  $("coldnote-go").onclick = on ? () => handOff(s) : null;
  $("coldnote-t").textContent = on
    ? `The prompt cache has expired, so your next message rewrites all ${fmtTokens(s.context_tokens)} tokens of this context, about 20 times what a warm turn reads.`
    : "";
  if (on && transcript.following) transcript.toBottom();
}

// Continue the session's work in a fresh one (session.handoff, #354); the
// old tab is archived once the new session has answered.
async function handOff(s) {
  try {
    const r = await app.callFor(s.key, "session.handoff");
    go(`#s=${app.keyOf(s.server, r.log_id)}`);
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}

function drawReplies(s) {
  const box = $("replies");
  const working = s.state === "working";
  const replies = working ? [] : s.replies || [];
  // The agent's brief for a fresh session, offered until a handoff starts.
  const brief = !working && !s.continued_by ? s.handoff || null : null;
  const key = JSON.stringify([s.log_id, replies, brief, s.continued_by]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !replies.length && !brief && !s.continued_by;
  // A press keeps the focus where it is (#428): on a phone the focused box is
  // two lines taller, and its blur moved the chip from under the finger
  // before the click, so the first tap did nothing.
  const keepFocus = (e) => e.preventDefault();
  const chips = replies.map((text) => {
    const b = document.createElement("button");
    b.className = "rp";
    b.textContent = text;
    b.addEventListener("mousedown", keepFocus);
    b.addEventListener("click", () => sendLine(text, false));
    return b;
  });
  if (brief) {
    const h = document.createElement("button");
    h.className = "rp hand";
    h.textContent = `continue fresh · ${fmtTokens(s.context_tokens)}`;
    h.addEventListener("mousedown", keepFocus);
    h.addEventListener("click", () => handOff(s));
    chips.unshift(h);
  }
  $("replies-pills").replaceChildren(...chips);
  const next = s.continued_by && app.state.sessions.get(app.keyOf(s.server, s.continued_by));
  $("handoff-done").hidden = !s.continued_by;
  const who = document.createElement(next ? "a" : "span");
  who.textContent = next ? next.handle : s.continued_by?.includes("@") ? s.continued_by : "a fresh session";
  if (next) who.href = `#s=${app.keyOf(s.server, s.continued_by)}`;
  $("handoff-done").replaceChildren(
    ...(!s.continued_by
      ? []
      : s.archived
        ? ["continued in ", who]
        : ["continued in ", who, "; this tab takes no messages and is archived when it finishes its first turn"]),
  );
  // Outside the rows the transcript watches for growth: follow the end here.
  if (transcript.following) transcript.toBottom();
}

// The shown session's message written offline (outbox.js), after the last row.
const QUEUED_LABEL = {
  queued: "queued · goes out when the connection is back",
  sending: "sending…",
  stale: "The session moved on since you wrote this",
  gone: "The session is closed",
};
function drawQueued() {
  const box = $("queued");
  const d = app.state.shown && outbox.draftFor(app.state.shown);
  box.hidden = !d;
  if (!d) return;
  for (const st of ["sending", "stale", "gone", "error"]) box.classList.toggle(st, d.state === st);
  // A refusal the person should read, word for word: a quota gate, an owner.
  $("queued-lbl").textContent = d.state === "error" ? `Not sent: ${d.error}` : QUEUED_LABEL[d.state] || QUEUED_LABEL.queued;
  box.querySelector(".text").textContent = d.text;
  if (transcript.following) transcript.toBottom();
}
teardown.push(outbox.on(drawQueued));
outbox.load();
// A queued draft's send gets the composer's deadline: with no answer the socket
// is dropped, which settles the send as unreached and the draft waits again.
outbox.setSender((key, params, { ask: asking = false } = {}) => {
  const call = app.callFor(key, "session.send", params);
  const late = setTimeout(() => app.conn.drop(), SEND_VERDICT_MS);
  // A stopped session on an untested harness: a background flush settles the
  // draft as an error; the person's Send anyway asks, and Cancel keeps it.
  return call
    .finally(() => clearTimeout(late))
    .catch(async (e) => {
      if (!asking || e.code !== "harness_untested" || !e.data) throw e;
      const pick = await untestedAsk(e.data);
      if (!pick) throw e;
      return app.callFor(key, "session.send", { ...params, untested: pick });
    });
});
$("queued").addEventListener("click", (ev) => {
  const act = ev.target.closest("[data-act]")?.dataset.act;
  const key = app.state.shown;
  const d = key && outbox.draftFor(key);
  if (!act || !d) return;
  if (act === "discard") outbox.discard(key);
  if (act === "send") outbox.sendAnyway(key).catch((e) => toast(e.message, { kind: "error" }));
  if (act === "copy")
    navigator.clipboard.writeText(d.text).then(
      () => toast("Copied"),
      () => toast("Could not copy", { kind: "error" }),
    );
  if (act === "edit") {
    input.value = input.value ? `${d.text}\n\n${input.value}` : d.text;
    localStorage.setItem(`aegis.draft.${key}`, input.value);
    autosize();
    input.focus();
    outbox.discard(key);
  }
});

// On a phone Close sits in the drawer, so a session whose agent said done gets
// an Archive button after its last row, one tap from where the reading ended.
// The stylesheet shows it only in the phone layout (#333).
function drawClosing(s) {
  const box = $("closing");
  const hide = s.attention !== "done" || locked(s);
  if (box.hidden === hide) return;
  box.hidden = hide;
  if (transcript.following) transcript.toBottom();
}
$("closing-btn").addEventListener("click", () => {
  const s = focused();
  if (s) closeSession(s);
});

const askClose = (s) =>
  ask(`Archive ${s.title || s.handle}? Its tab goes away in every browser; Reopen in the archive brings it back.`, { ok: "Archive" });
// The one rule for when archiving asks: a done session has nothing left to
// lose, anything else may be mid-work. The panel's Archive, /archive and /close,
// the Fleet's Archive button, the menu and the phone's Archive row all pass
// through it.
const confirmClose = async (s) => s.attention === "done" || (await askClose(s));

async function archiveGroup(members) {
  if (offline() || !(await ask(`Archive these ${members.length} sessions: ${members.map((m) => m.handle).join(", ")}?`, { ok: "Archive" }))) return;
  for (const m of members) app.callFor(m.key, "session.close").catch((e) => toast(`${m.handle}: ${e.message}`, { kind: "error" }));
}

async function closeSession(s) {
  if (locked(s)) return toast(`${s.handle} is a step of a running workflow; it archives when the run ends`, { kind: "error" });
  if (offline() || !(await confirmClose(s))) return;
  if (isTool(s)) {
    const { server, log_id } = app.forKey(s.key);
    const op = s.kind === "term" ? "term.close" : "browser.shut";
    return app.conn.call(op, { id: log_id }, server).catch((e) => toast(e.message, { kind: "error" }));
  }
  try {
    await app.callFor(s.key, "session.close");
  } catch (e) {
    sidebar?.fail(e.message);
  }
}

// A line from the composer, or from the menu's own filter (Alt+/ over a
// draft), which leaves the composer alone. The server resolves "/" lines.
// `target` is the session the line was typed in: a send that waited on
// uploads may finish after the person moved to another tab.
async function sendLine(text, fromComposer, attachments = [], target = focused()) {
  const s = target;
  if ((!text && !attachments.length) || !s) return;
  if (text === "/help" || text === "/workflow") {
    // The menu is the help: it lists every command with what it does. And
    // /workflow alone lists the saved workflows, which complete its id.
    const at = text === "/help" ? "/" : "/workflow ";
    if (fromComposer) menu.setLine(at);
    menu.close();
    if (fromComposer) menu.openInline();
    else menu.openOverlay(at);
    return;
  }
  // /help with words opens the Help tab searched for them (help.js).
  const asked = /^\/help\s+(.+)$/s.exec(text);
  if (asked) {
    if (fromComposer) {
      input.value = "";
      autosize();
      localStorage.removeItem(`aegis.draft.${s.key}`);
    }
    menu.close();
    helpTab.ask(asked[1].trim());
    go("#help");
    return;
  }
  if ((text === "/archive" || text === "/close") && !(await confirmClose(s))) return;
  if (text === "/restart" && !(await confirmRestart(s))) return;
  // With no way to the session's server, plain text waits as its draft on this
  // device (outbox.js); a command or files still need the connection.
  const rev = app.state.shown === s.key ? transcript.rev : -1;
  const waitAsDraft = async (nonce) => {
    await outbox.queue(s.key, text, rev, nonce);
    if (fromComposer) {
      localStorage.removeItem(`aegis.draft.${s.key}`);
      spoken.delete(`aegis.draft.${s.key}`); // the outbox keeps no mark
    }
    if (fromComposer && app.state.shown === s.key) {
      input.value = "";
      autosize();
      menu.close();
      $("composer").classList.remove("bad");
    }
    if (app.state.shown === s.key) transcript.toBottom();
  };
  if ((!app.conn.open || s.off) && !text.startsWith("/") && !attachments.length) return waitAsDraft();
  const box = $("replies");
  const was = box.hidden;
  const aside = /^\/btw(\s|$)/.test(text);
  if (!aside) {
    box.hidden = true; // any send answers the turn the pills belonged to
    delete box.dataset.key; // so the next drawReplies always redraws
  }
  const params = attachments.length ? { text, attachments } : { text };
  // A linked server older than nonces would refuse the field.
  if (fromComposer && !app.forKey(s.key).server) params.nonce = nonceFor(s.key, JSON.stringify([text, attachments]));
  // Only to this server, like the nonce: a linked one older than the field would refuse it.
  if (fromComposer && !app.forKey(s.key).server && spoken.has(`aegis.draft.${s.key}`)) params.dictated = true;
  const call = app.callFor(s.key, "session.send", params);
  const sent = (r) => {
    unconfirmed.delete(s.key);
    if (/^\/model\s/.test(text)) menu.forget(s.key); // its efforts may differ
    if (fromComposer) {
      localStorage.removeItem(`aegis.draft.${s.key}`);
      spoken.delete(`aegis.draft.${s.key}`);
      atts.clear(s.key);
    }
    if (fromComposer && app.state.shown === s.key) {
      input.value = "";
      autosize();
      // Clearing the box fires no input event; an open menu would take the next Esc.
      menu.close();
      $("composer").classList.remove("bad");
      $("send-error").textContent = "";
    }
    if (app.state.shown === s.key) transcript.toBottom();
    // /tutorial and /whats-new start a guide in a new session (help.guide):
    // open its tab, on the server that runs it, as a spawn from the new tab does.
    if (/^\/(tutorial|whats-new|doctor|handoff)(\s|$)/.test(text) && r?.log_id) {
      // A handoff across a link answers with the server it went to.
      const far = r.server && r.server !== app.conn.server ? r.server : app.forKey(s.key).server;
      go(`#s=${app.keyOf(far, r.log_id)}`);
    }
  };
  if (fromComposer) {
    sending.add(s.key);
    drawSending();
    if (app.state.shown === s.key) $("send-error").textContent = "";
  }
  try {
    sent(await (fromComposer ? answered(call) : call));
  } catch (e) {
    box.hidden = was;
    if (e?.code === "harness_untested" && e.data) {
      // A stopped session, or a /spawn line, on an untested harness version.
      if (fromComposer) {
        sending.delete(s.key);
        drawSending();
      }
      try {
        const r = await untestedRetry(
          () => Promise.reject(e),
          (untested) => app.callFor(s.key, "session.send", { ...params, untested }),
        );
        if (r !== CANCELLED) sent(r);
      } catch (e2) {
        toast(e2.message, { kind: "error" });
      }
      return;
    }
    if (e !== LATE) {
      toast(e.message, { kind: "error" });
      return;
    }
    // No answer yet. Plain text waits as the session's draft under the same
    // nonce, and the stalled socket is dropped so the page reconnects; the
    // draft goes out on the reconnect and arrives once.
    if (!text.startsWith("/") && !attachments.length) {
      call.catch(() => {}); // the drop rejects it; the draft carries on
      unconfirmed.delete(s.key);
      await waitAsDraft(params.nonce);
      app.conn.drop();
      return;
    }
    // A command or files: the box is the person's again, with the text, and
    // says so. An answer that comes later still settles it, unless they rewrote it.
    if (app.state.shown === s.key) $("send-error").textContent = NOT_CONFIRMED;
    const still = () => (app.state.shown === s.key ? input.value.trim() : localStorage.getItem(`aegis.draft.${s.key}`)?.trim()) === text;
    call.then(
      (r) => still() && sent(r),
      (e) => still() && app.state.shown === s.key && ($("send-error").textContent = `Not sent: ${e.message}`),
    );
  } finally {
    if (fromComposer) {
      sending.delete(s.key);
      drawSending();
    }
  }
}

// Sessions whose send waits on its uploads: a second Enter there does nothing.
const waiting = new Set();

// A new tab's first message when it carried files: they upload into the
// session just started, then go out with the text, as a composer send would.
async function sendFirst(key, text, files, dictated) {
  atts.add(files, key);
  waiting.add(key);
  try {
    await atts.settled(key);
  } finally {
    waiting.delete(key);
  }
  const ready = atts.ready(key);
  if (ready.error) {
    toast(ready.error, { kind: "error" });
    return;
  }
  try {
    await app.callFor(key, "session.send", { text, attachments: ready.ids, ...(dictated && text && { dictated }) });
    atts.clear(key);
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}
const send = async () => {
  await dictation.finish(input); // what was said goes out with the rest
  const s = focused();
  if (!s || waiting.has(s.key) || sending.has(s.key)) return;
  // The text and the session as they were at Enter, so a send that waits on
  // uploads still goes where and as it was typed.
  const text = input.value.trim();
  if (atts.list(s.key).length) {
    // Enter during an upload sends when it finishes, never without the files.
    waiting.add(s.key);
    $("send-error").textContent = "sending when the attachments finish…";
    try {
      await atts.settled(s.key);
    } finally {
      waiting.delete(s.key);
    }
    if (app.state.shown === s.key) $("send-error").textContent = "";
  }
  const ready = atts.ready(s.key);
  if (ready.error) {
    toast(ready.error, { kind: "error" });
    return;
  }
  return sendLine(text, true, ready.ids, s);
};

// A session's menu: its commands, and the tutorial pages /tutorial completes.
// A linked server without help.pages offers none. The part keeps the catalogs
// per session, so keystrokes that arrive while one loads wait for the same
// call instead of each asking the server again.
async function loadCommands(key) {
  const [cat, pages, agents] = await Promise.all([
    app.callFor(key, "commands.list"),
    app.conn.call("help.pages", {}, app.forKey(key).server).catch(() => []),
    spawnAgents(app.forKey(key).server),
  ]);
  return { ...cat, agents, tutorials: pages.filter((p) => p.kind === "tutorial") };
}
// What /spawn completes (#352): the agents of `server`, its default first,
// and on the home server each linked server's as `agent@server`. A link that
// is down, or refuses the call (a member's), offers none.
async function spawnAgents(server) {
  const roster = async (srv, far) => {
    const r = await app.conn.call("agents.list", {}, srv).catch(() => null);
    if (!r) return [];
    const on = far ? ` · on ${srv}` : "";
    return r.agents
      .filter((a) => a.enabled)
      .sort((a, b) => (b.name === r.default) - (a.name === r.default))
      .map((a) => ({ v: far ? `${a.name}@${srv}` : a.name, model: a.model, doc: `${a.harness} · ${a.model}${on}`, source: "" }));
  };
  const far = server ? [] : app.state.links.filter((l) => l.state === "linked").map((l) => roster(l.name, true));
  return (await Promise.all([roster(server, false), ...far])).flat();
}

// The composer's menu is a part (b4): it builds the list under the box and
// keeps its catalogs; the page hands it the line and the keys.
const menu = new CommandsPart(app, $("composer"), {
  fetch: loadCommands,
  run: (line) => sendLine(line, false),
  getLine: () => input.value,
  setLine: (v) => {
    input.value = v;
    autosize();
    if (app.state.shown) localStorage.setItem(`aegis.draft.${app.state.shown}`, v);
    input.focus();
  },
  meta: () => focused(),
});

for (const [id, cmd] of [
  ["chip-model", "model"],
  ["chip-effort", "effort"],
  ["chip-perm", "permission"],
]) {
  $(id).classList.add("click");
  $(id).addEventListener("click", () => {
    // Over a draft, the menu's own filter line, as Alt+/ does, so the draft stays.
    if (input.value && !input.value.startsWith("/")) return menu.openOverlay(`/${cmd} `);
    input.value = `/${cmd} `;
    input.focus();
    menu.openInline();
  });
}

// A session action's error: in the panel when it is the shown session's,
// else a toast (a card's or a tab's menu acts on any session).
function failed(key, e) {
  if (key === app.state.shown && sidebar) sidebar.fail(e.message);
  else toast(e.message, { kind: "error" });
}

async function interruptKey(key) {
  const s = app.state.sessions.get(key);
  if (!s || s.state !== "working") return;
  try {
    await app.callFor(key, "session.interrupt");
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}
const interrupt = () => focused() && interruptKey(focused().key);

async function quietKey(key, on) {
  if (offline() || !app.state.sessions.has(key)) return;
  try {
    await app.callFor(key, on ? "session.quiet" : "session.unquiet");
  } catch (e) {
    failed(key, e);
  }
}

// Autoclose (#691): the agent may then end only done with close, review or
// needs_you, so a session it finishes closes itself.
async function autocloseKey(key, on) {
  if (offline() || !app.state.sessions.has(key)) return;
  try {
    await app.callFor(key, "session.autoclose", { on });
  } catch (e) {
    failed(key, e);
  }
}

// Autohandoff (#1019): past its context size, the agent's turn_end with a
// brief continues the session in a fresh one, with what it waits on.
async function autohandoffKey(key, on) {
  if (offline() || !app.state.sessions.has(key)) return;
  try {
    await app.callFor(key, "session.autohandoff", { on });
  } catch (e) {
    failed(key, e);
  }
}

// Sleep ends the process and keeps the conversation. What would die with it
// (session.kills: background tasks, timers, processes it left running)
// is asked about first. A server older than #479 has no such op.
async function sleepKey(key) {
  const s = app.state.sessions.get(key);
  if (offline() || !s) return;
  // A check that fails cannot say nothing would die: ask then too.
  const kills = s.old || s.state === "asleep" ? [] : await app.callFor(key, "session.kills").catch(() => ["aegis could not check what would end with it"]);
  if (kills.length && !(await ask(`Sleep ${s.handle}? ${kills.join("; ")}.`, { ok: "Sleep" }))) return;
  try {
    await app.callFor(key, "session.stop");
  } catch (e) {
    failed(key, e);
  }
}

// Wake starts an asleep session's process and sends nothing (session.warm),
// asking first about an untested harness version, as a send does.
async function wakeKey(key) {
  const s = app.state.sessions.get(key);
  if (offline() || !s) return;
  try {
    // An older server's warm takes only log_id.
    const wake = s.old ? {} : { wake: true };
    await untestedRetry(
      () => app.callFor(key, "session.warm", wake),
      (untested) => app.callFor(key, "session.warm", { ...wake, untested }),
    );
  } catch (e) {
    failed(key, e);
  }
}

// Restart: a fresh process on the same conversation. It cuts a turn, so a
// working session asks first: from the menu, the panel and a typed /restart.
const confirmRestart = async (s) =>
  s.state !== "working" || (await ask(`Restart ${s.handle}? It cuts the turn it is working on.`, { ok: "Restart" }));
async function restartKey(key) {
  const s = app.state.sessions.get(key);
  if (offline() || !s) return;
  if (!(await confirmRestart(s))) return;
  try {
    await untestedRetry(
      () => app.callFor(key, "session.restart"),
      (untested) => app.callFor(key, "session.restart", { untested }),
    );
  } catch (e) {
    failed(key, e);
  }
}

// A nudge after an interrupt, an error or a sleep: a message to an asleep
// session wakes it, so Continue is one sentence to the agent.
const continueKey = (key) => !offline() && app.state.sessions.has(key) && sendLine("Continue", false, [], app.state.sessions.get(key));

// The first keystroke to a parked session starts its harness while the person
// types (session.warm); once per parked stretch (warmed, above).
function warmIfParked() {
  const s = app.state.shown && app.state.sessions.get(app.state.shown);
  if (!s || s.asleep_why !== "idle" || warmed === app.state.shown) return;
  warmed = app.state.shown;
  const key = app.state.shown;
  app.callFor(key, "session.warm", { log_id: app.forKey(key).log_id }).catch(() => {
    if (warmed !== key) return;
    warmed = null;
    if (app.state.shown === key) drawComposerHint(app.state.sessions.get(key));
  });
  drawComposerHint(s);
}
input.addEventListener("input", async () => {
  warmIfParked();
  autosize();
  if (app.state.shown) localStorage.setItem(`aegis.draft.${app.state.shown}`, input.value);
  if (!input.value.trim()) spoken.delete(`aegis.draft.${app.state.shown}`); // a box emptied by hand starts over
  const v = input.value;
  if (v.startsWith("/") && !v.startsWith("//")) {
    if (menu.isOpen) menu.refresh();
    else await menu.openInline(); // the outline below needs the catalog it loads
  } else if (menu.isOpen) menu.close();
  const now = input.value;
  $("composer").classList.toggle("bad", now.startsWith("/") && !now.startsWith("//") && !menu.known(now));
});
input.addEventListener("keydown", (ev) => {
  if (menu.onKey(ev)) return;
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !touch.matches) {
    ev.preventDefault();
    send();
  }
});
$("send").addEventListener("click", send);
$("interrupt").addEventListener("click", interrupt);

// -- the panel: a drawer on a phone (client.css, max-width 760px), collapsible
// and resizable on a desktop (side.js, through the sidebar part) --------------
const drawerMode = matchMedia("(max-width: 760px)");
// Closing the drawer returns to the desktop state this browser keeps. With no
// panel built (sidebar:false) both are no-ops, and the navigator hides the
// button (css/client.css).
const closeSide = () => sidebar?.closeDrawer();
const toggleSide = () => sidebar?.toggle();
// A monitor's link to the tool call it waits on: the row open, as find opens it.
const revealToolRow = (id) => {
  if (!transcript.entries.has(id)) return false;
  transcript.reveal(id);
  transcript.open(id);
  if (drawerMode.matches) closeSide();
  return true;
};

// The navigator floats just above the composer, which grows with its lines.
observe(new ResizeObserver(() => root.style.setProperty("--composer-h", `${$("composer").offsetHeight}px`)), $("composer"));
$("nav-side").addEventListener("click", toggleSide);
// The dimmed transcript is the session view's own ::after, so a tap on it
// lands on the view itself and goes no further.
root.querySelector(".v-session").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget && root.dataset.side === "open") closeSide();
});

// -- rename in place -------------------------------------------------------------
// The panel owns the title and handle inputs (parts/sidebar.js); a menu's
// Rename… opens the session and asks the panel for the handle's input, once the
// panel shows that session.
async function renameKey(key) {
  await openCard(key);
  const s = app.state.sessions.get(key);
  if (!s || !sidebar) return; // no panel, nothing in place to rename
  for (let i = 0; i < 30 && sidebar.handleText() !== s.handle; i++) await new Promise(requestAnimationFrame);
  sidebar.startRename("handle");
}

// -- groups by hand (#1039) -----------------------------------------------------
// A group's key on this server, as `group.set` takes it; null for one on a
// linked server, whose membership this server cannot edit.
function bareGroup(key) {
  const here = header.tabActions.here;
  return key.endsWith(`@${here}`) ? key.slice(0, -here.length - 1) : null;
}

// Every open member of the group `key`, quiet sessions and workers included.
function groupMembers(key) {
  const here = header.tabActions.here;
  return app.state.ordered.filter((m) => groupKey(m, here) === key);
}

// The groups this server may edit, by name, as the menu lists them.
function localGroups() {
  const here = header.tabActions.here;
  const by = new Map();
  for (const m of app.state.ordered) {
    if (m.server || !m.group || !m.group_name) continue;
    by.set(`${m.group}@${here}`, m.group_name);
  }
  return [...by].map(([key, name]) => ({ key, name }));
}

// Move tabs named by their keys into `group` ("new", a bare group key, or null
// to leave). The server publishes every member's new card, and the bar follows.
function moveToGroup(keys, group) {
  if (keys.some((k) => app.forKey(k).server)) return undefined;
  const log_ids = keys.map((k) => app.forKey(k).log_id);
  return app.conn
    .call("group.set", { log_ids, group })
    .catch((e) => toast(e.message, { kind: "error" }));
}

async function renameGroup(key, name) {
  const bare = bareGroup(key);
  if (!bare) return;
  try {
    await app.conn.call("group.rename", { group: bare, name });
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}

function ungroupGroup(key) {
  const members = groupMembers(key);
  if (!members.length) return undefined;
  return app.conn
    .call("group.set", { log_ids: members.map((m) => app.forKey(m.key).log_id), group: null })
    .catch((e) => toast(e.message, { kind: "error" }));
}

// The group head's own menu (#1039): Rename group, Ungroup, Archive group.
function groupMenu(key) {
  const name = groupMembers(key).find((m) => m.group_name)?.group_name || key;
  const local = !!bareGroup(key);
  const why = local ? (offline() ? app.t("tabs.noConnection") : undefined) : app.t("tabs.farGroup");
  return {
    title: app.t("tabs.groupTitle", { name }),
    groups: [
      [
        { id: "renamegroup", label: app.t("tabs.renameGroup"), icon: "pencil", disabled: why, run: () => header.startRename(key) },
        { id: "ungroup", label: app.t("tabs.ungroup"), icon: "close", disabled: why, run: () => ungroupGroup(key) },
      ],
      [
        { id: "archivegroup", label: app.t("tabs.archiveGroup"), icon: "archive", danger: true, disabled: why, run: () => archiveGroup(groupMembers(key)) },
      ],
    ],
  };
}

// -- the card and tab menu (menu.js) --------------------------------------------
// What applies to the session's state, and what the viewer may do: another
// member's session offers only opening it; a guard that blocks an action
// (a workflow's step, no connection) leaves it disabled with the reason.
function sessionMenu(key) {
  const s = app.state.sessions.get(key);
  if (!s) return null;
  const opening = [
    { id: "open", label: "Open", icon: "arrow", run: () => openCard(key) },
    { id: "newtab", label: "Open in new tab", icon: "open", run: () => window.open(hashOf(key)) },
  ];
  const title = `${isTool(s) ? s.name : s.handle} · ${LABEL[s.attention] || s.state}`;
  const why = locked(s)
    ? `${s.handle} is a step of a running workflow`
    : offline() || s.off
      ? "No connection to its server"
      : undefined;
  const close = [{ id: "archive", label: "Archive", icon: "archive", danger: true, disabled: why, run: () => closeSession(s) }];
  // Moving a session in or out of a group is one server op (#1039). The menu
  // lists the move instead of a submenu (menu.js has none): New group, then
  // each of this server's groups by name.
  const far = !!app.forKey(key).server;
  const frozen = far ? app.t("tabs.farGroup") : why;
  const current = groupKey(s, header.tabActions.here);
  const move = [
    { id: "moveto-new", label: app.t("tabs.newGroup"), icon: "start", disabled: frozen, run: () => moveToGroup([key], "new") },
    ...localGroups()
      .filter((g) => g.key !== current)
      .map((g) => ({
        id: `moveto-${g.key}`,
        label: app.t("tabs.moveTo", { name: g.name }),
        icon: "folder",
        disabled: frozen,
        run: () => moveToGroup([key], bareGroup(g.key)),
      })),
  ];
  if (s.group) move.push({ id: "leavegroup", label: app.t("tabs.leaveGroup"), icon: "arrow", disabled: frozen, run: () => moveToGroup([key], null) });
  if (isTool(s)) return { title, groups: [opening, close] };
  if (s.quiet || s.stay_quiet) opening.push({ id: "unquiet", label: "Unquiet", icon: "unquiet", disabled: why, run: () => quietKey(key, false) });
  else opening.push({ id: "quiet", label: "Quiet", icon: "quiet", disabled: why, run: () => quietKey(key, true) });
  const life = [];
  if (s.state === "working") life.push({ id: "interrupt", label: "Interrupt", icon: "interrupt", disabled: why, run: () => interruptKey(key) });
  // A server older than #479 wakes only a parked session.
  if (s.state === "asleep" && (!s.old || s.asleep_why === "idle")) life.push({ id: "wake", label: "Wake", icon: "wake", disabled: why, run: () => wakeKey(key) });
  if (s.state !== "working") life.push({ id: "continue", label: "Continue", icon: "play", disabled: why, run: () => continueKey(key) });
  if (s.state !== "asleep") life.push({ id: "sleep", label: "Sleep", icon: "sleep", disabled: why, run: () => sleepKey(key) });
  if (!s.old) life.push({ id: "restart", label: "Restart", icon: "restart", disabled: why, run: () => restartKey(key) });
  if (!s.worker) life.push({ id: "handoff", label: "Hand off", icon: "handoff", disabled: why, run: () => handOff(s) });
  const rename = [{ id: "rename", label: "Rename…", icon: "pencil", disabled: why, run: () => renameKey(key) }];
  rename.push({ id: "autoclose", label: s.autoclose ? "Autoclose off" : "Autoclose on", icon: "done", disabled: why, run: () => autocloseKey(key, !s.autoclose) });
  if (!s.worker) rename.push({ id: "autohandoff", label: s.autohandoff ? "Autohandoff off" : "Autohandoff on", icon: "handoff", disabled: why, run: () => autohandoffKey(key, !s.autohandoff) });
  return { title, groups: [opening, life, rename, move, close] };
}
// The menu of a card or tab pressed; none on the space around them.
// A group's own card or open stack's header in the Fleet gets the group's
// menu, as its tab does (#1039).
function cardMenu(t) {
  if (t.matches?.(".card.group[data-group]")) return groupMenu(t.dataset.group);
  if (t.matches?.(".gopen > .gh")) return groupMenu(t.parentElement.dataset.group);
  return t.dataset?.id && t.matches(".card, .tab, .wait") ? sessionMenu(t.dataset.id) : null;
}
function attachCardMenu(box, opts = {}) {
  cmenu.attach(box, cardMenu, { selector: ".card[data-id], .tab[data-id], .wait[data-id], .card.group[data-group], .gopen > .gh", ...opts });
}
// The tab card (#586) belongs to the header part (b2): a session tab's Fleet
// card on hover, with the tab menu's items as buttons, and a long press on a
// touch screen. The same card for a session the shown one waits on (#667), to
// the left of
// the panel and level with its row, where the panel's own cards open. The row
// is the sidebar part's; the card is the page's until b8.
if (sidebar) {
  const waitCard = attachTabCard(sidebar.waiting, {
    meta: (key) => app.state.sessions.get(key),
    card: (m) => fleetCard(m, openCard, (k) => app.state.sessions.get(k) && closeSession(app.state.sessions.get(k)), app.t),
    items: sessionMenu,
    item: ".wait",
    place: (box, r) => {
      box.style.left = `${Math.max(8, sidebar.el.getBoundingClientRect().left - box.offsetWidth - 12)}px`;
      box.style.top = `${Math.max(12, Math.min(r.top - 8, innerHeight - box.offsetHeight - 12))}px`;
    },
  });
  attachCardMenu(sidebar.waiting, { touch: (t) => t.matches(".wait[data-id]") && waitCard.sheet(t) });
}


// What a selection in the transcript offers (spec 05). The text is taken now:
// a touch can clear the selection before a button's click lands.
function selectionItems(range) {
  const text = rangeText(range);
  const s = app.state.shown && app.state.sessions.get(app.state.shown);
  if (!text || !s) return null;
  // Another member's session takes no message from this person (drawSending),
  // so the items that write to its composer are left out, as sessionMenu does.
  return [
    { id: "ask", label: "Ask", icon: "prompt", run: () => insertIntoComposer(quoted(text)) },
    { id: "aside", label: "Ask aside", icon: "btw", run: () => insertIntoComposer(quoted(text), { prefix: "/btw\n" }) },
    {
      id: "new",
      label: "New session",
      icon: "start",
      run: () => spawn.openWith({ server: s.server, agent: s.agent, model: s.model, cwd: s.cwd, from: s.server ? null : s.key, prompt: `From ${s.handle}:\n${quoted(text)}\n\n` }),
    },
    {
      id: "copy",
      label: "Copy",
      icon: "copy",
      title: "Copy (Shift: as a quote)",
      run: (ev) => navigator.clipboard.writeText(ev?.shiftKey ? quoted(text) : text).catch((e) => toast(`Copy failed: ${e.message || e}`, { kind: "error" })),
    },
  ].filter(Boolean);
}
cmenu.bar($("entries"), selectionItems);

// What a selection or an element in a file or a page offers (spec 07): the
// bar's items with the locator in front of the quote, and Comment. `to` is the
// session whose composer gets it; with none, or another member's, New session
// alone writes it. `leave` runs first (the viewer closes over the composer).
function pointItems({ locator, text, to, comment, leave = () => {} }) {
  const s = to && app.state.sessions.get(to);
  const own = !!s;
  const q = `${locator}\n${quoted(text)}`;
  return [
    own && { id: "ask", label: "Ask", icon: "prompt", run: () => (leave(), toComposer(to, q)) },
    own && { id: "aside", label: "Ask aside", icon: "btw", run: () => (leave(), toComposer(to, q, { prefix: "/btw\n" })) },
    { id: "new", label: "New session", icon: "start", run: () => newSessionWith(s, q) },
    comment && { id: "comment", label: "Comment", icon: "comment", run: comment },
    {
      id: "copy",
      label: "Copy",
      icon: "copy",
      title: "Copy (Shift: as a quote)",
      run: (ev) => navigator.clipboard.writeText(ev?.shiftKey ? q : text).catch((e) => toast(`Copy failed: ${e.message || e}`, { kind: "error" })),
    },
  ].filter(Boolean);
}

// A gesture inside a sandboxed page (pagepoint.js, through artifacts.js): the
// menu at the point it reports, or the bar over its selection, drawn here in
// the frame's place. Which file, session and tray it belongs to comes from
// where the frame is: Files' page, the viewer's, or a card's in the session.
//
// A page's script may post at any time. A press there leaves the focus in
// the frame (a right-click's mousedown gives it, and a long press focuses its
// frame), so a post while the person types in the composer or only rests the
// pointer over the page opens nothing. A page can take the focus by script,
// so that is not proof: what keeps a made-up proposal harmless is that its
// menu never takes the focus, and only a click on an item writes anything, and not in its first half second: the page chose
// where the menu or the bar opens, and could put Ask under a click aimed at
// the page. A page scrolled, Esc in it, or a selection gone
// closes only what that frame drew.
const PAGE_MENU_SETTLE_MS = 500; // a page's menu or bar takes no click this soon
let pageMenus = 0; // numbers each page menu, for its open and closed messages
function pagePoint(frame, { kind, x, y, rect, touch, locator = {}, text = "" }) {
  if (kind === "clear") {
    if (cmenu.current()?.target === frame) cmenu.close();
    return cmenu.framed(frame) && cmenu.unbar();
  }
  if (document.activeElement !== frame || !frame.checkVisibility({ visibilityProperty: true })) return;
  const files = frame.closest("#files");
  const inViewer = frame.closest("#viewer");
  const path = files
    ? filesView.path
    : frame.dataset.artifact || (inViewer && viewer.pathOf()) || decodeURIComponent(new URL(frame.src, location.href).pathname.split("/").pop());
  // The tray as it is when Comment runs: the view may have drawn a new one.
  const trayNow = () => (files ? filesView.tray : inViewer ? viewer.tray : null);
  const tray = trayNow();
  const quote = String(text).trim();
  if (!path || !quote) return;
  const where = [locator.heading && `§ ${locator.heading}`, locator.css].filter(Boolean).join(" › ");
  const items = pointItems({
    locator: `\`${path}\`${where ? ` ${where}` : ""}`,
    text: quote,
    to: files ? lastShown : app.state.shown,
    leave: inViewer ? () => viewer.close() : undefined,
    comment: tray && (() => trayNow()?.add({ locator: where || `\`${path}\``, quote, at: locator })),
  });
  const box = frame.getBoundingClientRect();
  if (kind === "select" && rect) {
    const r = new DOMRect(box.left + rect.left, box.top + rect.top, rect.right - rect.left, rect.bottom - rect.top);
    return cmenu.barAt(items, r, frame, PAGE_MENU_SETTLE_MS);
  }
  if (kind !== "menu") return;
  // Numbered: opening this one closes the one before, whose closed message
  // reaches the page after this open would, and must not undo it.
  const n = ++pageMenus;
  cmenu.open({ groups: [items] }, { x: box.left + x, y: box.top + y, touch }, frame, {
    focus: false,
    settle: PAGE_MENU_SETTLE_MS,
    onClose: () => artifacts.tell(frame, "aegis/point-closed", { n }),
  });
  artifacts.tell(frame, "aegis/point-open", { n });
}

// `text` into session `key`'s composer, going there first; Files' unsaved
// edits ask before it leaves. No session to go to: the New session form.
// True when the text landed.
async function toComposer(key, text, opts) {
  const s = key && app.state.sessions.get(key);
  if (!s) return newSessionWith(null, text);
  // A window tab (#w=) follows the session too, but draws no composer.
  if (app.state.shown !== key || route().view !== "session") {
    if (filesView.leaving(route(hashOf(key))) && !(await filesView.confirmLeave())) return false;
    go(hashOf(key));
  }
  if (app.state.shown !== key || route().view !== "session") return false;
  insertIntoComposer(text, opts);
  return true;
}

// The New session form with `text` for its prompt: from session `s`'s agent,
// model and folder, or the form's own picks. The text is in the form at once.
function newSessionWith(s, text) {
  if (s) spawn.openWith({ server: s.server, agent: s.agent, model: s.model, cwd: s.cwd, from: s.server ? null : s.key, prompt: `From ${s.handle}:\n${text}\n\n` });
  else spawn.prefill(text);
  return true;
}

// A transcript row's menu (spec 06), by kind; a selection inside the row puts
// the bar's items on top. Kinds the spec names no menu for keep the browser's,
// unless a selection is in them.
function rowMenu(t, ev) {
  const id = t.dataset?.id;
  const e = id && transcript.entries.get(id);
  const s = app.state.shown && app.state.sessions.get(app.state.shown);
  if (!e || !s) return null;
  const sel = getSelection();
  const range = sel.rangeCount && !sel.isCollapsed ? clampToRow(sel.getRangeAt(0), t) : null;
  // Only a press inside the selection is about it: Chrome on macOS selects
  // the word under any right-click. The keyboard's menu (no event) takes it.
  const hit = (r) => ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
  const picked = range && (!ev || [...range.getClientRects()].some(hit)) && selectionItems(range);
  // A tool's output is a round trip away: if the person moved to another
  // session meanwhile, the quote is dropped rather than written to that
  // session's composer.
  const quote = (then) => {
    const from = app.state.shown;
    rowText(id, copyDeps).then(
      (text) => {
        if (app.state.shown !== from) return toast("The quote was dropped: you left the session it came from.");
        if (text) then(quoted(text.trim()));
      },
      (err) => toast(err.message, { kind: "error" }),
    );
  };
  const reply = { id: "reply", label: "Reply", icon: "prompt", run: () => quote((q) => insertIntoComposer(q)) };
  const fresh = {
    id: "new",
    label: "New session",
    icon: "start",
    run: () => quote((q) => spawn.openWith({ server: s.server, agent: s.agent, model: s.model, cwd: s.cwd, from: s.server ? null : s.key, prompt: `From ${s.handle}:\n${q}\n\n` })),
  };
  const copy = { id: "copy", label: "Copy", icon: "copy", key: "c", run: () => copyRow(id, transcript.nodes.get(id), copyDeps) };
  // A part still streaming has an id the whole block will replace: no link.
  const link = !e.live && {
    id: "link",
    label: "Copy link",
    icon: "link",
    run: () => navigator.clipboard.writeText(`${location.origin}${location.pathname}#s=${app.state.shown}&at=${id}`).catch((err) => toast(`Copy failed: ${err.message || err}`, { kind: "error" })),
  };
  let groups = null;
  if (e.kind === "prose" || e.kind === "inbox") groups = [[reply, fresh], [copy, link]];
  else if (e.kind === "user") {
    const edit = { id: "edit", label: "Edit and resend", icon: "pencil", run: () => insertIntoComposer(e.md || "") };
    groups = [[edit, fresh], [copy, link]];
  } else if (e.kind === "tool") {
    const open = e.detail?.path && { id: "open", label: "Open file", icon: "file", run: () => openToolFile(id) };
    const out = { id: "copyout", label: "Copy output", icon: "copy", key: "c", run: copy.run };
    groups = [[open, out], [reply, link]];
  } else if (e.kind === "file" || e.kind === "artifact") {
    const items = sentItems(t).filter(Boolean);
    if (items.length) groups = [items];
  }
  groups = groups?.map((g) => g.filter(Boolean));
  if (!groups && !picked) return null;
  return { groups: [picked || [], ...(groups || [])] };
}
cmenu.attach($("entries"), rowMenu, { selector: ".row[data-id]" });

// A sent card's menu, from the buttons its bar drew (entries.js): Dock shows
// only where the person may dock it, and the shown file's key is on it.
function sentItems(row) {
  const dock = row.querySelector(".fbar .btn.dock");
  const id = dock?.dataset.dock;
  const can = id && !dock.hidden;
  const dl = row.querySelector(".fbar a.btn.dl");
  return [
    can && { id: "dock-right", label: "Dock right", icon: "dock", run: () => canvas.dock(id) },
    can && { id: "dock-left", label: "Dock left", icon: "dock", run: () => canvas.move(id, "left") },
    id && canvas.writable && !canvas.merged() && { id: "window", label: "Open in new window", icon: "window", run: () => canvas.popOut(id) },
    dl && { id: "download", label: "Download", icon: "latest", run: () => dl.click() },
  ];
}

// A Read, Write or Edit row's file, in the viewer (file.peek, as its Show button).
async function openToolFile(id) {
  try {
    // A linked server's file is fetched under /via and opens remote (#1044).
    const { log_id, server } = app.forKey(app.state.shown);
    const files = await app.conn.call("file.peek", { log_id, entry_id: id }, server);
    if (server) return viewer.open({ files: files.map((f) => paneFile(f).file), at: 0, remote: true, glyph: "file" });
    for (const f of files) if (f.root_path) peekRoots.set(f.url, f.root_path);
    viewer.open({ files, at: 0, remote: false, glyph: "file" });
  } catch (err) {
    toast(err.message, { kind: "error" });
  }
}
// The transcript's own scroll listener runs first: it still follows after a
// scroll it made, or one the browser made when a remounted row shrank the list.
cmenu.follow($("tr"), () => transcript.following);

// -- the host's contract (m6) ----------------------------------------------
// open() moves the router through the same go()/render() a tab click does;
// focus() puts the caret in this layout's own box. The mount delegates here
// once its build queue from m4 has drained.
app.hooks = { open: openSession, focus: () => input.focus() };

// Everything that outlives the root: the shown session's subscription, the
// outbox's redraw, the timers the page armed, and the parts the layout built
// (they are not the mount's `parts`). destroy() runs this once, in order.
teardown.push(() => unsubTranscript?.());
teardown.push(() => {
  clearInterval(bannerTimer);
  clearTimeout(fleetSave);
  clearTimeout(trSave);
  if (navFrame) cancelAnimationFrame(navFrame);
});
teardown.push(
  () => terminal.destroy(),
  () => terminalPart.destroy(),
  () => browserTab.close(),
  () => browserPart.destroy(),
  () => header.destroy(),
  () => sidebar?.destroy(),
  () => views.destroy(),
  () => fleet.destroy(),
  () => spawn.destroy(),
  () => dictation.destroy(),
  () => menu.destroy(),
  () => speaker.stop(),
  () => ruler.remove(),
);
return () => teardown.splice(0).forEach((off) => off());
}
