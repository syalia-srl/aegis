// The ribbon on a shared tab: a thin bar at the top of the page naming the
// agents that have it, and while one acts, what it does and a ring around the
// element it touches. Pause makes the person the tab's holder until Resume;
// after an agent's handover the bar turns red with its reason and a Done
// button. The worker injects it into a shared tab's isolated world
// (chrome.scripting) and calls aegisRibbon(state), or with null to remove
// it. It lives in a closed shadow root, so the page's styles and scripts
// cannot reach it; only its buttons take clicks, and only real ones.

if (!globalThis.aegisRibbon) {
  const host = document.createElement("aegis-ribbon");
  // Not part of the page: agents' snapshots and the page's own tree skip it.
  host.setAttribute("aria-hidden", "true");
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
:host{all:initial}
.bar{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:2147483647;display:flex;gap:6px;align-items:center;
  max-width:80vw;padding:2px 4px 3px 10px;border:1px solid var(--accent);border-top:0;border-radius:0 0 7px 7px;
  background:var(--surface);color:var(--ink);font:12px/1.4 system-ui,sans-serif;box-shadow:0 2px 8px #0004;
  white-space:nowrap;pointer-events:none;opacity:.94}
.bar.held{border-color:var(--err);opacity:1}
.bar.held .dot{background:var(--err)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none}
.txt{overflow:hidden;text-overflow:ellipsis}
.act{color:var(--accent)}
.ask{color:var(--err);font-weight:600}
.act:empty,.ask:empty{display:none}
button{pointer-events:auto;font:inherit;font-size:11px;padding:0 8px;border-radius:5px;cursor:pointer;
  border:1px solid var(--accent);background:transparent;color:var(--ink)}
.held button{border-color:var(--err);background:var(--err);color:var(--surface);font-weight:600}
.ring{position:fixed;z-index:2147483647;border:2px solid var(--accent);border-radius:5px;box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 30%,transparent);
  pointer-events:none;opacity:0;transition:opacity .25s}
.ring.on{opacity:1}
</style><div class="bar"><span class="dot"></span><b>aegis</b><span class="txt"><span class="who"></span> <span class="ask"></span><span class="act"></span></span><button></button></div><div class="ring"></div>`;
  const bar = root.querySelector(".bar");
  const who = root.querySelector(".who");
  const ask = root.querySelector(".ask");
  const act = root.querySelector(".act");
  const btn = root.querySelector("button");
  const ring = root.querySelector(".ring");
  let fade = 0;
  let next = "person";

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!e.isTrusted) return; // the page's scripts cannot press it
    chrome.runtime.sendMessage({ t: "hold", holder: next }).catch(() => {});
  });

  globalThis.aegisRibbon = (s) => {
    if (!s) {
      host.remove();
      return;
    }
    for (const k of ["accent", "surface", "ink", "err"]) if (s[k]) host.style.setProperty(`--${k}`, s[k]);
    if (!s.err) host.style.setProperty("--err", "#d9806f");
    host.style.setProperty("all", "initial", "important");
    if (!host.isConnected) document.documentElement.append(host);
    const held = s.holder === "person";
    bar.classList.toggle("held", held && !!s.ask);
    who.textContent = held && s.ask ? `· ${s.by || s.who.join(", ")} needs you:` : `· ${s.who.join(", ")}${held ? " · paused" : ""}`;
    ask.textContent = held && s.ask ? s.ask : "";
    btn.textContent = held ? (s.ask ? "Done" : "Resume") : "Pause";
    next = held ? "agent" : "person";
    act.textContent = s.act ? `· ${s.act}` : "";
    clearTimeout(fade);
    if (s.ring) {
      const pad = 3;
      Object.assign(ring.style, {
        left: `${s.ring.x - pad}px`,
        top: `${s.ring.y - pad}px`,
        width: `${s.ring.w + 2 * pad}px`,
        height: `${s.ring.h + 2 * pad}px`,
      });
      ring.classList.add("on");
    }
    if (s.act)
      fade = setTimeout(() => {
        ring.classList.remove("on");
        act.textContent = "";
      }, 2000);
  };
}
