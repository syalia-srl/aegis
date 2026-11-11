// The extension's service worker: one socket to the aegis server it is paired
// with (chrome.py on the server), kept open while the browser runs, and the
// authority over which agent may use which tab.
//
// Chrome stops an idle MV3 worker after 30 s; an open socket with traffic
// keeps it alive since Chrome 116, so it pings every 20 s. If the worker is
// stopped anyway, the alarm every minute, the browser starting and the popup
// opening each start it again, and it reconnects.
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
  return tab && { name: row.name, title: tab.title || "", url: tab.url || "", grants: row.grants.map((g) => g.log_id) };
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
  }
  await set({ shared: all });
  drawIcon();
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
      if (w) tabs.push(w);
      else delete all[id];
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
};

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  const run = POPUP[msg.t];
  if (!run) return;
  Promise.resolve(run(msg)).then(reply, (e) => reply({ error: String(e.message || e) }));
  return true;
});

// A shared tab that closes ends its grants; one that navigates tells the
// server its new title and address.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  if ((await shared())[tabId]) await unshare(tabId);
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (!change.title && !change.url) return;
  const row = (await shared())[tabId];
  if (row) send({ t: "tab", tab: await wire(tabId, row) });
});

chrome.alarms.create("reconnect", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
drawIcon();
connect();
