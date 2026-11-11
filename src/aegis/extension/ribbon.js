// The ribbon on a shared tab: a thin bar at the top of the page naming the
// agents that have it, and while one acts, what it does and a ring around the
// element it touches. The worker injects it into a shared tab's isolated
// world (chrome.scripting) and calls aegisRibbon(state), or with null to
// remove it. It lives in a closed shadow root, so the page's styles and
// scripts cannot reach it, and it takes no clicks: the page stays the
// person's.

if (!globalThis.aegisRibbon) {
  const host = document.createElement("aegis-ribbon");
  // Not part of the page: agents' snapshots and the page's own tree skip it.
  host.setAttribute("aria-hidden", "true");
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
:host{all:initial}
.bar{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:2147483647;display:flex;gap:6px;align-items:center;
  max-width:70vw;padding:2px 10px 3px;border:1px solid var(--accent);border-top:0;border-radius:0 0 7px 7px;
  background:var(--surface);color:var(--ink);font:12px/1.4 system-ui,sans-serif;box-shadow:0 2px 8px #0004;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none;opacity:.92}
.dot{width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none}
.act{color:var(--accent)}
.act:empty{display:none}
.ring{position:fixed;z-index:2147483647;border:2px solid var(--accent);border-radius:5px;box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 30%,transparent);
  pointer-events:none;opacity:0;transition:opacity .25s}
.ring.on{opacity:1}
</style><div class="bar"><span class="dot"></span><b>aegis</b><span class="who"></span><span class="act"></span></div><div class="ring"></div>`;
  const who = root.querySelector(".who");
  const act = root.querySelector(".act");
  const ring = root.querySelector(".ring");
  let fade = 0;

  globalThis.aegisRibbon = (s) => {
    if (!s) {
      host.remove();
      return;
    }
    for (const k of ["accent", "surface", "ink"]) if (s[k]) host.style.setProperty(`--${k}`, s[k]);
    host.style.setProperty("all", "initial", "important");
    if (!host.isConnected) document.documentElement.append(host);
    who.textContent = `· ${s.who.join(", ")}`;
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
