// The extension's service worker: one socket to the aegis server it is paired
// with (chrome.py on the server), kept open while the browser runs.
//
// Chrome stops an idle MV3 worker after 30 s; an open socket with traffic
// keeps it alive since Chrome 116, so it pings every 20 s. If the worker is
// stopped anyway, the alarm every minute, the browser starting and the popup
// opening each start it again, and it reconnects.
//
// chrome.storage.local holds the pairing: the server's address, the device
// token, the server's name and the last theme it sent. The popup reads it and
// asks the worker for the socket's state.

import { MARK } from "./aegis/js/mark.js";

const PING_MS = 20_000;
const BAD_TOKEN = 4401; // web.py: the token was revoked or never valid

let ws = null;
let state = "offline"; // offline | connecting | connected
let pinger = 0;
let lastError = "";

const wsUrl = (server) => server.replace(/^http/, "ws").replace(/\/$/, "") + "/chrome/socket";

function setState(s, error = "") {
  state = s;
  lastError = error;
  chrome.runtime.sendMessage({ t: "status", state, error }).catch(() => {});
}

async function connect() {
  const { server, token } = await chrome.storage.local.get(["server", "token"]);
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
  sock.onopen = () => {
    sock.send(JSON.stringify({ t: "hello", token, version: chrome.runtime.getManifest().version }));
  };
  sock.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.t === "welcome") {
      await chrome.storage.local.set({ serverName: msg.server, deviceName: msg.name, ...(msg.theme && Object.keys(msg.theme).length ? { theme: msg.theme } : {}) });
      clearInterval(pinger);
      pinger = setInterval(() => sock.readyState === WebSocket.OPEN && sock.send(JSON.stringify({ t: "ping" })), PING_MS);
      setState("connected");
    } else if (msg.t === "theme") {
      await chrome.storage.local.set({ theme: msg.vars });
    } else if (msg.t === "unpaired") {
      await forget("Unpaired from aegis.");
    }
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
  await chrome.storage.local.remove(["token", "deviceName"]);
  if (ws) {
    const sock = ws;
    ws = null;
    sock.close();
  }
  setState("offline", why);
}

// The toolbar icon, drawn still: in the theme's accent while a tab is shared,
// in grey while none is. Chrome redraws an action's icon only when asked.
function drawIcon(color) {
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
  return chrome.action.setIcon({ imageData: out });
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.t === "state") {
    connect();
    reply({ state, error: lastError });
  } else if (msg.t === "connect") {
    connect().then(() => reply({ state, error: lastError }));
    return true;
  } else if (msg.t === "unpair") {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ t: "unpair" }));
    forget("").then(() => reply({ state }));
    return true;
  }
});

chrome.alarms.create("reconnect", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
drawIcon("#8a867b").catch(() => {});
connect();
