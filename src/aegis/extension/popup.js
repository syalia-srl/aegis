// The popup: pairing with a code, then the server it is paired with and
// whether the socket is open (background.js keeps it).

import { gorgoneion } from "./aegis/js/glyphs.js";

const $ = (id) => document.getElementById(id);
const SAYS = { connected: "Connected", connecting: "Connecting…", offline: "Offline" };

$("mark").append(gorgoneion());

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

async function draw(status) {
  const s = await chrome.storage.local.get(["server", "token", "serverName", "theme"]);
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
    draw(await chrome.runtime.sendMessage({ t: "connect" }));
  } catch (e) {
    $("err").textContent = e instanceof TypeError ? `No aegis answered at ${server}.` : e.message;
  } finally {
    $("go").disabled = false;
  }
});

$("unpair").addEventListener("click", async () => {
  draw(await chrome.runtime.sendMessage({ t: "unpair" }));
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.t === "status") draw(msg);
});
chrome.storage.onChanged.addListener((changes) => {
  if (changes.theme) applyTheme(changes.theme.newValue);
});

chrome.runtime.sendMessage({ t: "state" }).then(draw, () => draw(null));
