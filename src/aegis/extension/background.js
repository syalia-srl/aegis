// The extension's service worker: one socket to the aegis server it is paired
// with (chrome.py on the server), kept open while the browser runs, and the
// authority over which agent may use which tab.
//
// Chrome stops an idle MV3 worker after 30 s; an open socket with traffic
// keeps it alive since Chrome 116, so it pings every 20 s. If the worker is
// stopped anyway, the alarm every minute, the browser starting and the popup
// opening each start it again, and it reconnects.
//
// While a tab has a grant the worker holds chrome.debugger on it and runs the
// agents' calls there (driving, below), and draws the ribbon (ribbon.js).
//
// chrome.storage.local holds the pairing (the server's address, the device
// token, the server's name, the last theme), the grants (`shared`: tab id ->
// its name and the sessions it is granted to) and the agents' requests still
// waiting. A tab with no grant is never named to the server. The popup reads
// storage and sends the worker what the person does.

import { MARK } from "./aegis/js/mark.js";

const PING_MS = 20_000;
const BAD_TOKEN = 4401; // web.py: the token was revoked or never valid
const GREY = "#8a867b";

let ws = null;
let state = "offline"; // offline | connecting | connected
let pinger = 0;
let lastError = "";
let asked = 0; // a reply id for the server's answers
const waiting = new Map(); // reply id -> resolve

const wsUrl = (server) => server.replace(/^http/, "ws").replace(/\/$/, "") + "/chrome/socket";
const get = (keys) => chrome.storage.local.get(keys);
const set = (o) => chrome.storage.local.set(o);
// Whether the message went: false while the socket is not open.
function send(msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(msg));
  return true;
}

function setState(s, error = "") {
  state = s;
  lastError = error;
  chrome.runtime.sendMessage({ t: "status", state, error }).catch(() => {});
}

// -- grants ---------------------------------------------------------------------

async function shared() {
  return (await get("shared")).shared || {};
}

// A tab's name for agents: the site's own word (chat.qwen.ai -> qwen), with a
// number when another shared tab has it already. Stable while it is shared.
function nameFor(url, taken) {
  let host = "";
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch {}
  if (host === "localhost" || /^[\d.]+$|^\[/.test(host)) host = "localhost";
  const parts = host.split(".").filter(Boolean);
  const base = (parts.length > 1 ? parts[parts.length - 2] : parts[0] || "tab").toLowerCase().replace(/[^a-z0-9-]/g, "") || "tab";
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base}-${n}`;
  return name;
}

async function wire(tabId, row) {
  const tab = await chrome.tabs.get(+tabId).catch(() => null);
  return tab && { name: row.name, title: tab.title || "", url: tab.url || "", grants: row.grants.map((g) => g.log_id), holder: row.holder || "agent", ask: row.ask || "" };
}

// The "aegis" tab group, in the accent: every shared tab joins it.
async function group(tabId) {
  const { groupId } = await get("groupId");
  const live = groupId != null && (await chrome.tabGroups.get(groupId).catch(() => null));
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return;
  if (live && live.windowId === tab.windowId) return chrome.tabs.group({ tabIds: [tabId], groupId });
  const id = await chrome.tabs.group({ tabIds: [tabId], createProperties: { windowId: tab.windowId } });
  await chrome.tabGroups.update(id, { title: "aegis", color: "orange" });
  await set({ groupId: id });
}

async function share(tabId, sessions, request = null) {
  const all = await shared();
  const tab = await chrome.tabs.get(tabId);
  const taken = new Set(Object.entries(all).filter(([id]) => +id !== tabId).map(([, r]) => r.name));
  const row = all[tabId] || { name: nameFor(tab.url, taken), grants: [] };
  for (const s of sessions) if (!row.grants.some((g) => g.log_id === s.log_id)) row.grants.push({ log_id: s.log_id, handle: s.handle });
  all[tabId] = row;
  await set({ shared: all });
  await group(tabId).catch(() => {});
  const w = await wire(tabId, row);
  sessions.forEach((s, i) => send({ t: "grant", tab: w, log_id: s.log_id, ...(i === 0 && request ? { request } : {}) }));
  drawIcon();
  attach(tabId).catch(() => {});
  ribbon(tabId);
}

// End one grant, or all of a tab's; `tell` is false when the server asked.
async function unshare(tabId, logId = null, tell = true) {
  const all = await shared();
  const row = all[tabId];
  if (!row) return;
  row.grants = logId ? row.grants.filter((g) => g.log_id !== logId) : [];
  if (tell) send({ t: "revoke", tab: row.name, log_id: logId });
  if (!row.grants.length) {
    delete all[tabId];
    chrome.tabs.ungroup(+tabId).catch(() => {});
    detach(+tabId);
  }
  await set({ shared: all });
  drawIcon();
  ribbon(+tabId);
}

// -- requests -------------------------------------------------------------------

async function requests() {
  return (await get("requests")).requests || [];
}

async function setRequests(list) {
  await set({ requests: list });
  chrome.action.setBadgeText({ text: list.length ? String(list.length) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#d9806f" });
}

async function dropRequest(id) {
  await setRequests((await requests()).filter((r) => r.id !== id));
}

// The tabs whose title or address hold a word of a request, best first.
async function matches(req) {
  const tabs = (await chrome.tabs.query({})).filter((t) => /^https?:/.test(t.url || ""));
  const words = req.match.toLowerCase().split(/[^\p{L}\p{N}.]+/u).filter((w) => w.length > 2);
  const score = (t) => words.filter((w) => `${t.title} ${t.url}`.toLowerCase().includes(w)).length;
  return { tabs, hits: tabs.map((t) => [score(t), t]).filter(([n]) => n > 0).sort((a, b) => b[0] - a[0]).map(([, t]) => t) };
}

// What the popup offers for a request: the matches, or every tab when none
// matches, since there the person sees and picks the one they give.
async function candidates(req) {
  const { tabs, hits } = await matches(req);
  return (hits.length ? hits : tabs).map((t) => ({ id: t.id, title: t.title, url: t.url, favIconUrl: t.favIconUrl }));
}

// Give a request: the tab the person picked in the popup, or, for a Give
// pressed in aegis (tabId null), the best match only. With no match nothing
// is given: a tab the person never saw named is never handed over, and the
// request waits in the popup for them to pick one.
async function give(id, tabId = null) {
  const req = (await requests()).find((r) => r.id === id);
  if (!req) return;
  const who = [{ log_id: req.log_id, handle: req.handle }];
  if (req.kind === "open") {
    await dropRequest(id);
    const tab = await chrome.tabs.create({ url: req.url });
    return share(tab.id, who, id);
  }
  tabId ??= (await matches(req)).hits[0]?.id ?? null;
  if (tabId == null) return send({ t: "nomatch", request: id });
  await dropRequest(id);
  return share(tabId, who, id);
}

async function refuse(id) {
  await dropRequest(id);
  send({ t: "deny", request: id });
}

// -- driving ------------------------------------------------------------------------

// An agent's browser_* call on a granted tab, over chrome.debugger: the same
// CDP calls Playwright makes for the server's browsers, so the events are
// trusted, as the person's are. The debugger is attached while a tab has a
// grant; Chrome shows its "is being debugged" bar meanwhile, and should.

class Fail extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const attached = new Set();
const dbg = (tabId, method, params = {}) => chrome.debugger.sendCommand({ tabId }, method, params);
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
let TARGET = null; // target.js's text, run inside the page

async function attach(tabId) {
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (e) {
    // Attached before the worker restarted: the session is still ours.
    if (!/already attached/i.test(e.message)) throw new Fail("cannot_drive", `this tab cannot be driven: ${e.message}`);
  }
  attached.add(tabId);
  for (const m of ["Page.enable", "Runtime.enable", "Network.enable"]) await dbg(tabId, m).catch(() => {});
}

async function detach(tabId) {
  attached.delete(tabId);
  await chrome.debugger.detach({ tabId }).catch(() => {});
}

chrome.debugger.onDetach.addListener(({ tabId }) => attached.delete(tabId));

// The page's own log for the agents: navigations, console errors, failed
// requests and dialogs, as browser_events reads a server browser's.
const urls = new Map(); // requestId -> [method, url], for a failure's address
chrome.debugger.onEvent.addListener(async ({ tabId }, method, p) => {
  let ev = null;
  if (method === "Network.requestWillBeSent") {
    urls.set(p.requestId, [p.request.method, p.request.url]);
    if (urls.size > 2000) urls.delete(urls.keys().next().value);
  } else if (method === "Page.frameNavigated" && !p.frame.parentId) ev = { kind: "navigate", url: p.frame.url };
  else if (method === "Runtime.consoleAPICalled" && p.type === "error")
    ev = { kind: "console", level: "error", text: p.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 500) };
  else if (method === "Runtime.exceptionThrown")
    ev = { kind: "console", level: "error", text: (p.exceptionDetails.exception?.description || p.exceptionDetails.text || "").split("\n")[0].slice(0, 500) };
  else if (method === "Network.loadingFailed" && !p.canceled) {
    const [m, url] = urls.get(p.requestId) || ["", ""];
    ev = { kind: "network", method: m, url, status: p.errorText };
  } else if (method === "Network.responseReceived" && p.response.status >= 400)
    ev = { kind: "network", method: urls.get(p.requestId)?.[0] || "", url: p.response.url, status: p.response.status };
  else if (method === "Page.javascriptDialogOpening") ev = { kind: "dialog", type: p.type, text: p.message };
  if (!ev) return;
  const row = (await shared())[tabId];
  if (row) send({ t: "event", tab: row.name, event: ev, who: "page" });
});

// One run of target.js in an isolated world of the tab's page, which the
// page's scripts cannot see or tamper with. The element found stays in that
// world as `el` for the next steps of the same call.
async function world(tabId) {
  TARGET ??= await (await fetch(chrome.runtime.getURL("target.js"))).text();
  const { frameTree } = await dbg(tabId, "Page.getFrameTree");
  const { executionContextId } = await dbg(tabId, "Page.createIsolatedWorld", { frameId: frameTree.frame.id, worldName: "aegis" });
  const run = async (expr) => {
    const r = await dbg(tabId, "Runtime.evaluate", { expression: expr, contextId: executionContextId, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Fail("bad_target", (r.exceptionDetails.exception?.description || r.exceptionDetails.text).split("\n")[0].replace(/^Error: /, ""));
    return r.result.value;
  };
  await run(`${TARGET}; globalThis.T = aegisTarget(); 0`);
  return run;
}

// The first element a target names, waited for up to 5 s as the page loads,
// scrolled into view: its role, name, box and how many matched.
async function locate(tabId, target) {
  const run = await world(tabId);
  const q = JSON.stringify(target);
  for (let t = 0; ; t += 250) {
    const n = await run(`globalThis.all = T.find(${q}); all.length`);
    if (n) break;
    if (t >= 5000) throw new Fail("not_found", `nothing on the page matches ${target}`);
    await pause(250);
  }
  const info = await run(`globalThis.el = all[0]; el.scrollIntoView({block: "center", inline: "center"}); ({n: all.length, ...T.describe(el)})`);
  return { run, info };
}

// Wait for the tab to finish loading, after what an action may have started.
async function settle(tabId, ms = 5000) {
  await pause(300);
  for (let t = 0; t < ms; t += 100) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab) throw new Fail("revoked", "the tab was closed");
    if (tab.status === "complete") return { url: tab.url || "", title: tab.title || "" };
    await pause(100);
  }
  const tab = await chrome.tabs.get(tabId);
  return { url: tab.url || "", title: tab.title || "" };
}

const KEYS = {
  Enter: [13, "\r"], Tab: [9, ""], Escape: [27, ""], Backspace: [8, ""], Delete: [46, ""],
  ArrowUp: [38, ""], ArrowDown: [40, ""], ArrowLeft: [37, ""], ArrowRight: [39, ""],
  Home: [36, ""], End: [35, ""], PageUp: [33, ""], PageDown: [34, ""], Space: [32, " "],
};
const MODS = { Alt: 1, Control: 2, Meta: 4, Shift: 8 };
// Chords Chrome runs as editing commands, since a synthetic key does not.
const COMMANDS = { a: "selectAll", c: "copy", v: "paste", x: "cut", z: "undo", y: "redo" };

async function key(tabId, chord) {
  const parts = chord.split("+");
  let name = parts.pop() || "+";
  let modifiers = 0;
  for (const m of parts) {
    if (!(m in MODS)) throw new Fail("bad_key", `unknown modifier ${m} in ${chord}`);
    modifiers |= MODS[m];
  }
  let vk, text, code;
  if (KEYS[name]) {
    [vk, text] = KEYS[name];
    code = name;
    if (name === "Space") name = " ";
  } else if ([...name].length === 1) {
    const up = name.toUpperCase();
    vk = up.charCodeAt(0);
    code = /[A-Z]/.test(up) ? `Key${up}` : /\d/.test(name) ? `Digit${name}` : "";
    text = modifiers & MODS.Shift ? up : name;
  } else throw new Fail("bad_key", `unknown key ${name}`);
  const chordKey = modifiers & (MODS.Control | MODS.Meta);
  if (chordKey) text = "";
  const commands = chordKey && COMMANDS[name.toLowerCase()] ? [COMMANDS[name.toLowerCase()]] : [];
  const base = { key: name, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers };
  await dbg(tabId, "Input.dispatchKeyEvent", { ...base, type: text ? "keyDown" : "rawKeyDown", text, unmodifiedText: text, commands });
  await dbg(tabId, "Input.dispatchKeyEvent", { ...base, type: "keyUp" });
}

async function ribbon(tabId, extra = {}) {
  const [row, { theme }] = await Promise.all([shared().then((a) => a[tabId]), get("theme")]);
  const t = theme || {};
  const state = row
    ? { who: row.grants.map((g) => g.handle), holder: row.holder || "agent", ask: row.ask || "", by: row.by || "", accent: t["--accent"], surface: t["--surface"], ink: t["--ink"], err: t["--err"], ...extra }
    : null;
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["ribbon.js"] });
    await chrome.scripting.executeScript({ target: { tabId }, func: (s) => globalThis.aegisRibbon?.(s), args: [state] });
  } catch {} // a page no extension may touch, such as the Web Store
}

const DRIVE = {
  async snapshot(tabId) {
    const { nodes } = await dbg(tabId, "Accessibility.getFullAXTree");
    const tab = await chrome.tabs.get(tabId);
    return { url: tab.url || "", title: tab.title || "", tree: aria(nodes) };
  },
  async click(tabId, a, who) {
    const { info } = await locate(tabId, a.target);
    await ribbon(tabId, { act: `${who} clicks ${info.name || a.target}`, ring: info.rect });
    const x = info.rect.x + info.rect.w / 2;
    const y = info.rect.y + info.rect.h / 2;
    await dbg(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await dbg(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await dbg(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
    const out = await settle(tabId);
    return info.n > 1 ? { ...out, note: `${info.n} elements matched; clicked the first` } : out;
  },
  async type(tabId, a, who) {
    const { run, info } = await locate(tabId, a.target);
    await ribbon(tabId, { act: `${who} types into ${info.name || a.target}`, ring: info.rect });
    // Focus it and select what it holds, so the text replaces it.
    await run(`el.focus(); if (el.select) el.select(); else if (el.isContentEditable) { const r = document.createRange(); r.selectNodeContents(el); getSelection().removeAllRanges(); getSelection().addRange(r); } 0`);
    if (a.text) await dbg(tabId, "Input.insertText", { text: a.text });
    else await key(tabId, "Delete");
    if (a.submit) await key(tabId, "Enter");
    return { ...(await settle(tabId)), secret: info.secret };
  },
  async press(tabId, a, who) {
    if (a.target) {
      const { run, info } = await locate(tabId, a.target);
      await ribbon(tabId, { act: `${who} presses ${a.key}`, ring: info.rect });
      await run("el.focus(); 0");
    } else await ribbon(tabId, { act: `${who} presses ${a.key}` });
    await key(tabId, a.key);
    return settle(tabId);
  },
  async goto(tabId, a, who) {
    await ribbon(tabId, { act: `${who} goes to ${a.url || a.action}` });
    if (a.url) {
      if (!/^https?:\/\//i.test(a.url)) throw new Fail("bad_url", "only http and https addresses load in the person's tabs");
      const r = await dbg(tabId, "Page.navigate", { url: a.url });
      if (r.errorText) throw new Fail("goto_failed", r.errorText);
    } else if (a.action === "reload") await dbg(tabId, "Page.reload");
    else {
      const h = await dbg(tabId, "Page.getNavigationHistory");
      const e = h.entries[h.currentIndex + (a.action === "back" ? -1 : 1)];
      if (!e) throw new Fail("goto_failed", `there is no page to go ${a.action} to`);
      await dbg(tabId, "Page.navigateToHistoryEntry", { entryId: e.id });
    }
    return settle(tabId, 30_000);
  },
  async screenshot(tabId, a) {
    const params = { format: "png" };
    if (a.target) {
      const { run, info } = await locate(tabId, a.target);
      const [sx, sy] = await run("[scrollX, scrollY]");
      params.clip = { x: info.rect.x + sx, y: info.rect.y + sy, width: info.rect.w, height: info.rect.h, scale: 1 };
      params.captureBeyondViewport = true;
    } else if (a.full_page) {
      const m = await dbg(tabId, "Page.getLayoutMetrics");
      const c = m.cssContentSize || m.contentSize;
      params.clip = { x: 0, y: 0, width: c.width, height: c.height, scale: 1 };
      params.captureBeyondViewport = true;
    }
    // A tab in the background draws no frames, and the capture waits for one.
    const late = pause(15_000).then(() => {
      throw new Fail("screenshot_failed", "the tab drew nothing in 15 s; it may be hidden behind another tab");
    });
    const { data } = await Promise.race([dbg(tabId, "Page.captureScreenshot", params), late]);
    return { png: data };
  },
};

// The accessibility tree as text, in the shape a server browser's snapshot
// has: one "- role "name"" line per node, nested by two spaces. Nodes that
// only group others (generic, none) give their children to their parent.
const FLAT = new Set(["none", "generic", "presentation", "RootWebArea", "WebArea", "InlineTextBox", "LineBreak", "LabelText", "LayoutTable", "LayoutTableRow", "LayoutTableCell"]);
const VALUED = new Set(["textbox", "searchbox", "combobox", "spinbutton", "slider"]);

function aria(nodes) {
  const by = new Map(nodes.map((n) => [n.nodeId, n]));
  const lines = [];
  const walk = (n, depth) => {
    const role = n.role?.value || "";
    const name = String(n.name?.value || "").replace(/\s+/g, " ").trim();
    const kids = (n.childIds || []).map((id) => by.get(id)).filter(Boolean);
    if (n.ignored || FLAT.has(role)) return kids.forEach((k) => walk(k, depth));
    const pad = "  ".repeat(depth);
    if (role === "StaticText") {
      if (name) lines.push(`${pad}- text: ${name}`);
      return;
    }
    const props = Object.fromEntries((n.properties || []).map((p) => [p.name, p.value?.value]));
    let head = `${pad}- ${role}${name ? ` ${JSON.stringify(name)}` : ""}`;
    if (props.level) head += ` [level=${props.level}]`;
    if (props.checked === "true" || props.checked === true) head += " [checked]";
    if (props.disabled) head += " [disabled]";
    if (props.expanded === true) head += " [expanded]";
    const value = n.value?.value;
    if (VALUED.has(role) && value) head += `: ${JSON.stringify(String(value))}`;
    // Children that only spell the name again add nothing.
    const onlyText = kids.every((k) => k.ignored || k.role?.value === "StaticText" || k.role?.value === "InlineTextBox");
    if (!kids.length || (name && onlyText) || VALUED.has(role)) return lines.push(head);
    lines.push(`${head}:`);
    kids.forEach((k) => walk(k, depth + 1));
  };
  const root = nodes.find((n) => !n.parentId) || nodes[0];
  if (root) walk(root, 0);
  return lines.join("\n");
}

// A call from the server. The grant is checked here too: this side is the
// authority, and a server bug must not widen what an agent reaches.
async function onCall(msg) {
  const reply = (o) => send({ t: "result", id: msg.id, ...o });
  try {
    const all = await shared();
    const tabId = Object.keys(all).find((k) => all[k].name === msg.tab);
    const grant = tabId && all[tabId].grants.find((g) => g.log_id === msg.log_id);
    if (!grant) throw new Fail("not_granted", `chrome:${msg.tab} is not granted to you`);
    const run = DRIVE[msg.op];
    if (!run) throw new Fail("not_supported", `${msg.op} does not work on the person's tabs yet`);
    if (all[tabId].holder === "person" && !["snapshot", "screenshot"].includes(msg.op))
      throw new Fail("person_has_control", `the person holds chrome:${msg.tab}`);
    await attach(+tabId);
    reply({ ok: true, value: await run(+tabId, msg.args || {}, grant.handle) });
  } catch (e) {
    reply({ ok: false, code: e.code || "call_failed", message: String(e.message || e) });
  }
}

// -- holding ----------------------------------------------------------------------

// Who holds a shared tab: the agents, or the person after Pause in the ribbon
// or an agent's handover. The server refuses agents' actions while the person
// holds it, and wakes them when it comes back. `tell` is false when the
// server said so.
async function hold(tabId, holder, { ask = "", by = "", tell = true } = {}) {
  const all = await shared();
  const row = all[tabId];
  if (!row) return;
  row.holder = holder;
  row.ask = holder === "person" ? ask : "";
  row.by = holder === "person" ? by : "";
  await set({ shared: all });
  if (tell) send({ t: "hold", tab: row.name, holder });
  ribbon(+tabId);
  if (holder === "person" && ask) {
    chrome.notifications.create(`aegis-${tabId}`, { type: "basic", iconUrl: "icons/128.png", title: `${by || "An agent"} needs you`, message: ask, requireInteraction: true });
  } else chrome.notifications.clear(`aegis-${tabId}`);
}

// A click on the notification shows the tab it is about.
chrome.notifications.onClicked.addListener(async (id) => {
  const tabId = +id.replace(/^aegis-/, "");
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return;
  await chrome.tabs.update(tabId, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
});

// -- the socket -----------------------------------------------------------------

async function connect() {
  const { server, token } = await get(["server", "token"]);
  if (!server || !token || (ws && ws.readyState <= WebSocket.OPEN)) return;
  setState("connecting");
  let sock;
  try {
    sock = new WebSocket(wsUrl(server));
  } catch (e) {
    setState("offline", String(e.message || e));
    return;
  }
  ws = sock;
  sock.onopen = async () => {
    const all = await shared();
    // Tabs closed while the worker slept: their grants are gone.
    const tabs = [];
    for (const [id, row] of Object.entries(all)) {
      const w = await wire(id, row);
      if (w) {
        tabs.push(w);
        attach(+id).catch(() => {});
      } else delete all[id];
    }
    await set({ shared: all });
    sock.send(JSON.stringify({ t: "hello", token, version: chrome.runtime.getManifest().version, tabs }));
  };
  sock.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.t === "welcome") {
      await set({ serverName: msg.server, deviceName: msg.name, ...(msg.theme && Object.keys(msg.theme).length ? { theme: msg.theme } : {}) });
      await setRequests(msg.requests || []);
      clearInterval(pinger);
      pinger = setInterval(() => send({ t: "ping" }), PING_MS);
      setState("connected");
      drawIcon();
    } else if (msg.t === "theme") {
      await set({ theme: msg.vars });
      drawIcon();
    } else if (msg.t === "unpaired") {
      await forget("Unpaired from aegis.");
    } else if (msg.t === "sessions") {
      waiting.get(msg.id)?.(msg.sessions);
      waiting.delete(msg.id);
    } else if (msg.t === "request") {
      await setRequests([...(await requests()).filter((r) => r.id !== msg.request.id), msg.request]);
    } else if (msg.t === "cancel") {
      await dropRequest(msg.request);
    } else if (msg.t === "answer") {
      if (msg.give) await give(msg.request);
      else await dropRequest(msg.request);
    } else if (msg.t === "hold") {
      const all = await shared();
      const id = Object.keys(all).find((k) => all[k].name === msg.tab);
      if (id) await hold(id, msg.holder, { ask: msg.ask, by: msg.by, tell: false });
    } else if (msg.t === "call") {
      return onCall(msg);
    } else if (msg.t === "revoke") {
      const all = await shared();
      const id = Object.keys(all).find((k) => all[k].name === msg.tab);
      if (id) await unshare(id, msg.log_id, false);
    }
    if (!["pong", "sessions"].includes(msg.t)) chrome.runtime.sendMessage({ t: "changed" }).catch(() => {});
  };
  sock.onclose = async (ev) => {
    if (ws !== sock) return;
    ws = null;
    clearInterval(pinger);
    if (ev.code === BAD_TOKEN) await forget("This browser is no longer paired with that server.");
    else setState("offline", ev.code === 1000 ? "" : "The server cannot be reached.");
  };
}

// The token is gone: keep the address, so pairing again needs only a code.
async function forget(why) {
  for (const id of Object.keys(await shared())) chrome.tabs.ungroup(+id).catch(() => {});
  await chrome.storage.local.remove(["token", "deviceName", "shared"]);
  await setRequests([]);
  if (ws) {
    const sock = ws;
    ws = null;
    sock.close();
  }
  setState("offline", why);
  drawIcon();
}

function sessions() {
  return new Promise((resolve) => {
    const id = ++asked;
    waiting.set(id, resolve);
    if (!send({ t: "sessions", id })) {
      waiting.delete(id);
      resolve(null);
    }
  });
}

// -- the toolbar icon -------------------------------------------------------------

// Drawn still: in the theme's accent while a tab is shared, in grey while none
// is. Chrome redraws an action's icon only when asked.
async function drawIcon() {
  const [{ theme }, all] = await Promise.all([get("theme"), shared()]);
  const color = Object.keys(all).length ? theme?.["--accent"] || "#e0a872" : GREY;
  const out = {};
  for (const size of [16, 32]) {
    const c = new OffscreenCanvas(size, size);
    const g = c.getContext("2d");
    g.scale(size / 64, size / 64);
    for (const [, tag, attrs] of MARK.matchAll(/<(circle|path)([^>]*)\/>/g)) {
      const a = Object.fromEntries([...attrs.matchAll(/([a-z]+)="([^"]*)"/g)].map((m) => [m[1], m[2]]));
      const p = new Path2D();
      if (tag === "circle") p.arc(+a.cx, +a.cy, +a.r, 0, 2 * Math.PI);
      else p.addPath(new Path2D(a.d));
      // The eye is drawn in the page's background: knock it out.
      g.globalCompositeOperation = a.fill === "#11100e" ? "destination-out" : "source-over";
      g.fillStyle = color;
      g.fill(p);
    }
    out[size] = g.getImageData(0, 0, size, size);
  }
  return chrome.action.setIcon({ imageData: out }).catch(() => {});
}

// -- what the popup asks ----------------------------------------------------------

const POPUP = {
  state: async () => (connect(), { state, error: lastError }),
  connect: async () => (await connect(), { state, error: lastError }),
  unpair: async () => {
    send({ t: "unpair" });
    await forget("");
    return { state };
  },
  sessions: () => sessions(),
  share: (m) => share(m.tabId, m.sessions),
  unshare: (m) => unshare(m.tabId, m.log_id ?? null),
  candidates: async (m) => {
    const req = (await requests()).find((r) => r.id === m.request);
    return req ? candidates(req) : [];
  },
  give: (m) => give(m.request, m.tabId ?? null),
  refuse: (m) => refuse(m.request),
  // Pause, Resume and Done in a tab's ribbon, or on its row in the popup.
  hold: (m, sender) => hold(m.tabId ?? sender.tab?.id, m.holder === "person" ? "person" : "agent"),
};

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  const run = POPUP[msg.t];
  if (!run) return;
  Promise.resolve(run(msg, _sender)).then(reply, (e) => reply({ error: String(e.message || e) }));
  return true;
});

// A shared tab that closes ends its grants; one that navigates tells the
// server its new title and address.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  chrome.notifications.clear(`aegis-${tabId}`);
  if ((await shared())[tabId]) await unshare(tabId);
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (!change.title && !change.url && change.status !== "complete") return;
  const row = (await shared())[tabId];
  if (!row) return;
  if (change.status === "complete") ribbon(tabId);
  if (change.title || change.url) send({ t: "tab", tab: await wire(tabId, row) });
});

chrome.alarms.create("reconnect", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
drawIcon();
connect();
