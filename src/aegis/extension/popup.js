// The popup: pairing with a code; then the server it is paired with, the
// agents' requests waiting for an answer, and this window's tabs, each with a
// switch that shares it with chosen sessions (background.js keeps the grants).

import { gorgoneion } from "./aegis/js/glyphs.js";

const $ = (id) => document.getElementById(id);
const SAYS = { connected: "Connected", connecting: "Connecting…", offline: "Offline" };
const ask = (msg) => chrome.runtime.sendMessage(msg);
let status = null;
let picking = null; // the tab id whose session picker is open
const chosen = new Set(); // the sessions ticked in it, kept across redraws

$("mark").append(gorgoneion());

function h(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids.filter((k) => k != null && k !== false));
  return e;
}

const host = (url) => {
  try {
    return new URL(url).host;
  } catch {
    return url || "";
  }
};

function applyTheme(vars) {
  for (const [k, v] of Object.entries(vars || {})) document.body.style.setProperty(k, v);
}

// The browser's own name for the device row in aegis: Chrome, Edge, Brave…
function browserName() {
  const brands = navigator.userAgentData?.brands?.map((b) => b.brand) || [];
  const known = brands.find((b) => !/Not.?A.?Brand|Chromium/i.test(b));
  if (navigator.brave) return "Brave";
  return known?.replace(/^Google /, "").replace(/^Microsoft /, "") || "Chromium";
}

function platform() {
  const p = navigator.userAgentData?.platform || navigator.platform || "";
  return p.replace(/^Win.*/, "Windows").replace(/^Mac.*/, "macOS");
}

function favicon(tab) {
  return tab.favIconUrl && /^https?:|^data:/.test(tab.favIconUrl) ? h("img", { src: tab.favIconUrl, alt: "" }) : h("span", { className: "fav" });
}

// -- requests ---------------------------------------------------------------------

async function requestCard(req) {
  const card = h("div", { className: "req" });
  const what = req.kind === "open" ? `asks to open ${req.url}` : `asks for a tab: ${req.match}`;
  card.append(h("p", {}, h("b", { textContent: req.handle }), ` ${what}`));
  if (req.why) card.append(h("p", { className: "why", textContent: req.why }));
  let pick = null;
  if (req.kind !== "open") {
    const tabs = await ask({ t: "candidates", request: req.id });
    tabs.slice(0, 6).forEach((tab, i) => {
      const radio = h("input", { type: "radio", name: `rq-${req.id}`, value: String(tab.id), checked: i === 0 });
      card.append(h("label", {}, radio, h("span", { textContent: `${tab.title} · ${host(tab.url)}` })));
    });
    pick = () => +card.querySelector("input:checked")?.value || null;
  }
  const refuse = h("button", { className: "btn", type: "button", textContent: "Refuse" });
  refuse.onclick = () => ask({ t: "refuse", request: req.id }).then(draw);
  const give = h("button", { className: "btn primary", type: "button", textContent: req.kind === "open" ? "Open and give" : "Give" });
  give.onclick = () => ask({ t: "give", request: req.id, tabId: pick ? pick() : null }).then(draw);
  card.append(h("div", { className: "row" }, refuse, give));
  return card;
}

// -- this window's tabs -------------------------------------------------------------

async function picker(tab) {
  const box = h("div", { className: "pick" });
  const list = await ask({ t: "sessions" });
  if (!list) {
    box.append(h("p", { className: "err", textContent: "Not connected: sharing needs the server." }));
    return box;
  }
  if (!list.length) box.append(h("p", { className: "note", textContent: "No agent session is open in aegis." }));
  for (const s of list) {
    const tick = h("input", { type: "checkbox", value: s.log_id, checked: chosen.has(s.log_id) });
    tick.onchange = () => (tick.checked ? chosen.add(s.log_id) : chosen.delete(s.log_id));
    box.append(h("label", {}, tick, h("b", { textContent: s.handle }), h("span", { textContent: s.title })));
  }
  const go = h("button", { className: "btn primary", type: "button", textContent: "Share" });
  go.onclick = async () => {
    const sessions = list.filter((s) => chosen.has(s.log_id));
    if (!sessions.length) return;
    picking = null;
    chosen.clear();
    await ask({ t: "share", tabId: tab.id, sessions });
    draw();
  };
  const cancel = h("button", { className: "btn", type: "button", textContent: "Cancel" });
  cancel.onclick = () => {
    picking = null;
    chosen.clear();
    draw();
  };
  box.append(h("div", { className: "row" }, cancel, go));
  return box;
}

async function tabRow(tab, row) {
  const on = !!row;
  const sw = h("input", { type: "checkbox", className: "switch", checked: on || picking === tab.id, title: on ? "Stop sharing" : "Share with agents" });
  sw.onchange = async () => {
    if (on) await ask({ t: "unshare", tabId: tab.id });
    else picking = sw.checked ? tab.id : null;
    draw();
  };
  const el = h("div", { className: `tab${on ? " on" : ""}` }, favicon(tab), h("div", { className: "t" }, h("b", { textContent: on ? `${tab.title}` : tab.title }), h("span", { textContent: on ? `chrome:${row.name} · ${host(tab.url)}` : host(tab.url) })), sw);
  if (on) {
    const chips = h("div", { className: "chips" });
    for (const g of row.grants) {
      const x = h("button", { type: "button", textContent: "×", title: `Disconnect ${g.handle}` });
      x.onclick = () => ask({ t: "unshare", tabId: tab.id, log_id: g.log_id }).then(draw);
      chips.append(h("span", { className: "chip" }, g.handle, x));
    }
    const more = h("button", { className: "lnk", type: "button", textContent: "add an agent" });
    more.onclick = () => {
      picking = tab.id;
      draw();
    };
    chips.append(more);
    el.append(chips);
  }
  if (picking === tab.id) el.append(await picker(tab));
  return el;
}

// -- the page -----------------------------------------------------------------------

// One redraw at a time: each awaits the worker, and two interleaved would
// leave the older one's rows on screen.
let drawing = Promise.resolve();
function draw() {
  drawing = drawing.then(paint, paint);
  return drawing;
}

async function paint() {
  const s = await chrome.storage.local.get(["server", "token", "serverName", "theme", "shared", "requests"]);
  applyTheme(s.theme);
  const paired = !!s.token;
  $("paired").hidden = !paired;
  $("pair").hidden = paired;
  $("where").textContent = paired ? `${s.serverName || ""} · ${s.server}` : "not paired";
  if (!paired) {
    if (s.server && !$("server").value) $("server").value = s.server;
    if (status?.error) $("err").textContent = status.error;
    return;
  }
  const st = status?.state || "offline";
  $("dot").className = `dot${st === "connected" ? " on" : st === "connecting" ? " wait" : ""}`;
  $("state").textContent = SAYS[st];
  $("perr").textContent = st === "connected" ? "" : status?.error || "";

  const shared = s.shared || {};
  $("requests").replaceChildren(...(await Promise.all((s.requests || []).map(requestCard))));
  const tabs = (await chrome.tabs.query({ currentWindow: true })).filter((t) => /^https?:/.test(t.url || ""));
  $("tabs").replaceChildren(...(await Promise.all(tabs.map((t) => tabRow(t, shared[t.id])))));
  const n = Object.keys(shared).length;
  $("shared").textContent = n ? `${n} tab${n > 1 ? "s" : ""} shared. Agents see no other tab.` : "Nothing is shared. Agents see no tab of this browser.";
}

$("pair").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("err").textContent = "";
  $("go").disabled = true;
  const server = $("server").value.trim().replace(/\/+$/, "");
  try {
    const r = await fetch(`${server}/chrome/pair`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code: $("code").value, browser: browserName(), name: `${browserName()} on ${platform()}` }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.message || `The server answered ${r.status}.`);
    await chrome.storage.local.set({ server, token: body.token, serverName: body.server });
    $("code").value = "";
    status = await ask({ t: "connect" });
    draw();
  } catch (e) {
    $("err").textContent = e instanceof TypeError ? `No aegis answered at ${server}.` : e.message;
  } finally {
    $("go").disabled = false;
  }
});

$("unpair").addEventListener("click", async () => {
  status = await ask({ t: "unpair" });
  draw();
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.t === "status") status = msg;
  if (msg.t === "status" || msg.t === "changed") draw();
});
chrome.storage.onChanged.addListener((changes) => {
  if (changes.theme) applyTheme(changes.theme.newValue);
});

ask({ t: "state" }).then(
  (s) => {
    status = s;
    draw();
  },
  () => draw(),
);
