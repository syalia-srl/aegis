// Feeds in the session sidebar: a row each, and a card on hover, as monitors
// have (monitors.js), with what a feed's history says (#998). The counts,
// buckets and waits arrive decided (aegis/feeds.py card_stats); this file
// draws them, plus what moves with the clock: the debounce countdown and how
// long the oldest waiting item has waited.

import { age } from "./gauges.js";

const CARD_W = 392;
const OPEN_AFTER_MS = 220;
const CLOSE_AFTER_MS = 160;
const narrow = matchMedia("(max-width: 760px)");
const byMouse = (fn) => (ev) => ev.pointerType === "mouse" && fn();
const nowS = () => Date.now() / 1000;
const SVG = "http://www.w3.org/2000/svg";

function h(tag, cls, ...kids) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  for (const k of kids) if (k != null && k !== false) n.append(k);
  return n;
}

function clock(ts, seconds = false) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
  });
}

function dur(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : w.endsWith("ch") ? "es" : "s"}`;

// The row's right side: what waits, or why it does not wake, or when the last
// item came.
function rowRight(f) {
  if (f.paused) return f.errors >= 3 ? "source fails" : "paused";
  if (f.pending) return `${f.pending} waiting`;
  return f.last_new ? `last ${age(nowS() - f.last_new)} ago` : "nothing yet";
}

// The last hour, one bar per 5 minutes, the bucket now amber while items wait.
function spark(f) {
  const r = f.ranges?.hour;
  const box = h("div", "spark");
  if (!r) return box;
  const top = Math.max(3, ...r.counts);
  r.counts.forEach((v, i) => {
    const b = h("i", i === r.counts.length - 1 && f.pending ? "w" : null);
    b.style.height = `${Math.max(1, (v / top) * 14)}px`;
    box.append(b);
  });
  return box;
}

function row(f) {
  const box = h("div", "mon feed");
  box.dataset.id = f.name;
  box.tabIndex = 0;
  box.classList.toggle("bad", !!f.paused && f.errors >= 3);
  box.classList.toggle("pz", !!f.paused);
  const right = h("span", "r", rowRight(f));
  box.append(h("div", "kv", h("span", null, f.name), right), spark(f));
  box.tick = () => (right.textContent = rowRight(f));
  box.addEventListener("pointerenter", byMouse(() => {
    clearTimeout(closeT);
    clearTimeout(openT);
    openT = setTimeout(() => open(box.dataset.id), OPEN_AFTER_MS);
  }));
  box.addEventListener("pointerleave", byMouse(soonClose));
  box.addEventListener("focus", () => box.matches(":focus-visible") && open(f.name));
  box.addEventListener("click", () => open(f.name));
  return box;
}

// -- the card -----------------------------------------------------------------
const PILLS = {
  live: ["live", ""],
  debouncing: ["debouncing", ""],
  "waits for the turn": ["waits for the turn", "late"],
  paused: ["paused", "off"],
};

function bars(f, range) {
  const r = f.ranges[range];
  const n = r.counts.length;
  const W = 360, H = 70, gap = 2, bw = (W - gap * (n - 1)) / n;
  const top = Math.max(4, ...r.counts);
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 8}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const mark = (tag, attrs) => {
    const e = document.createElementNS(SVG, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    svg.append(e);
    return e;
  };
  mark("line", { x1: 0, x2: W, y1: H, y2: H, class: "grid" });
  const tip = h("div", "tip");
  tip.hidden = true;
  const unit = dur(r.size).replace(" 0s", "").replace("0m", "").trim();
  r.counts.forEach((v, i) => {
    const x = i * (bw + gap), bh = (v / top) * (H - 4);
    const last = i === n - 1;
    const rect = mark("rect", {
      x, y: H - bh, width: bw, height: v ? Math.max(bh, 1) : 0,
      class: `col${last && f.pending ? " w" : ""}`,
    });  // prettier-ignore
    if (r.batches[i]) mark("line", { x1: x + bw / 2, x2: x + bw / 2, y1: H + 2, y2: H + 7, class: "btick" });
    // A transparent column over the whole bucket, so an empty one has a tip too.
    const hit = mark("rect", { x, y: 0, width: bw, height: H + 8, class: "hit" });
    for (const t of [rect, hit]) {
      t.addEventListener("pointerenter", () => {
        tip.hidden = false;
        tip.style.left = `${((x + bw / 2) / W) * 100}%`;
        const waiting = last && f.pending ? `, ${f.pending} waiting` : "";
        tip.textContent = `${plural(v, "item")}${waiting} · ${plural(r.batches[i], "batch")}`;
      });
      t.addEventListener("pointerleave", () => (tip.hidden = true));
    }
  });
  const from = { hour: "60 min ago", day: "24 h ago", week: "7 days ago" }[range];
  return h(
    "div",
    "chart",
    svg,
    tip,
    h("div", "ax", h("span", null, from), h("span", null, `bars per ${unit} · ticks are batches`), h("span", null, "now")),
  );
}

function waits(f, range) {
  const q = f.ranges[range].wait;
  const box = h("div", "waits", h("h5", null, "Wait per item, from seen to delivered"));
  if (!q) box.append(h("div", "none", "nothing delivered in this range"));
  else {
    const stat = (v, k) => h("span", null, h("b", null, dur(v)), k);
    box.append(h("div", "q3", stat(q[0], "median"), stat(q[1], "90th pct"), stat(q[2], "max")));
  }
  const parts = [["debounce", "db"], ["turn", "tn", "turn running"], ["quota", "qt"], ["paused", "pz"]];
  const split = f.split || {};
  if (Object.keys(split).length) {
    const bar = h("div", "split"), lg = h("div", "lg");
    for (const [k, cls, label] of parts) {
      const v = split[k] || 0;
      if (!v) continue;
      const i = h("i", cls);
      i.style.width = `${(100 * v).toFixed(1)}%`;
      bar.append(i);
      lg.append(h("span", cls, `${label || k} ${Math.round(100 * v)}%`));
    }
    box.append(bar, lg);
  }
  const pb = f.per_batch || {};
  const cell = (v, k) => h("div", null, h("div", "v", v == null ? "–" : v), h("div", "k", k));
  const tokens = pb.tokens == null ? null : pb.tokens >= 1000 ? `${(pb.tokens / 1000).toFixed(1)}k` : String(pb.tokens);
  box.append(
    h(
      "div",
      "per",
      cell(pb.items == null ? null : String(pb.items), "items per batch"),
      cell(pb.turn_s == null ? null : dur(pb.turn_s), "turn per batch"),
      cell(tokens, "tokens per batch"),
    ),
  );
  return box;
}

function item(text, seen, right, cls) {
  const tx = h("span", "tx", text);
  tx.title = "Click to show all of it";
  tx.addEventListener("click", () => {
    if (!getSelection().toString()) tx.classList.toggle("x");
  });
  return h("div", `it${cls ? ` ${cls}` : ""}`, h("span", "ts", clock(seen)), tx, right);
}

function latest(f, live) {
  const box = h("div", "items", h("h5", null, "Latest items"));
  if (f.waiting?.length) {
    box.append(h("div", "bt", h("span", null, "waiting"), h("span", "r", plural(f.pending, "item"))));
    for (const w of f.waiting) {
      const wt = h("span", "wt");
      const draw = () => (wt.textContent = `waits ${dur(nowS() - w.seen)}`);
      draw();
      live.push(draw);
      box.append(item(w.text, w.seen, wt, "w"));
    }
  }
  let b = null;
  for (const r of f.recent || []) {
    if (r.batch !== b) {
      b = r.batch;
      const n = f.recent.filter((x) => x.batch === b).length;
      box.append(h("div", "bt", h("span", null, `batch#${b}`), h("span", "r", `${clock(r.sent)} · ${plural(n, "item")}`)));
    }
    box.append(item(r.text, r.seen, h("span", "wt", `waited ${dur(r.sent - r.seen)}`)));
  }
  if (!f.waiting?.length && !f.recent?.length) box.append(h("div", "none", "no item yet"));
  return box;
}

function source(f) {
  const bad = f.errors > 0;
  const res = bad
    ? `fails · ${f.errors} of 3`
    : f.kind === "file"
      ? `read · ${f.pos?.line != null ? `line ${f.pos.line}, ` : ""}byte ${(f.pos?.offset ?? 0).toLocaleString("en")}`
      : `ok · cursor ${f.pos?.cursor || "(none yet)"}`;
  const cmd = f.kind === "file" ? f.source.file : f.source.command;
  const pre = h("pre", null, cmd);
  pre.title = "Click to show all of it";
  pre.addEventListener("click", () => {
    if (!getSelection().toString()) pre.classList.toggle("x");
  });
  return h(
    "div",
    "checks",
    h("h5", null, "Source"),
    h(
      "div",
      `ck${bad ? " bad" : ""}`,
      h("div", "h", h("span", "k", f.kind), h("span", "res", res), f.polled_at && h("span", "ago", `read ${clock(f.polled_at, true)}`)),
      pre,
      f.source.filter && h("div", "err flt", `filter ${f.source.filter}`),
      bad && f.last_error && h("div", "err", f.last_error),
    ),
  );
}

function card(f, live) {
  const [label, cls] = f.paused && f.errors >= 3 ? ["source fails", "bad"] : PILLS[f.state] || [f.state, ""];
  const hd = h("div", "hd", h("div", "t", h("div", "d", f.name), h("span", `pill ${cls}`, label)), h("div", "sub", f.where));

  const range = ranges.get(f.name) || "day";
  const total = f.ranges[range].counts.reduce((a, b) => a + b, 0);
  const at = h("div", "at"), inn = h("div", "in"), e = h("div", "e", at, inn);
  const drawNext = () => {
    e.className = "e";
    if (f.paused) {
      e.className = "e none";
      at.textContent = f.errors >= 3 ? "paused by errors" : "paused";
      inn.textContent = f.pending ? `${plural(f.pending, "item")} kept for resume` : f.paused;
    } else if (!f.pending) {
      e.className = "e none";
      at.textContent = "nothing waiting";
      inn.textContent = f.last_new ? `last item ${clock(f.last_new)}` : "no item yet";
    } else if (f.state === "waits for the turn") {
      e.className = "e late";
      at.textContent = "when the turn ends";
      inn.textContent = `oldest waits ${dur(nowS() - f.oldest_pending)}`;
    } else {
      const quiet = f.debounce_s - (nowS() - f.last_new);
      const cap = 120 - (nowS() - f.oldest_pending);
      at.textContent = `in ${dur(Math.max(0, Math.min(quiet, cap)))}`;
      inn.textContent = "after a quiet debounce";
    }
  };
  drawNext();
  live.push(drawNext);
  const rng = h("div", "rng");
  for (const k of ["hour", "day", "week"]) {
    const b = h("button", k === range ? "on" : null, `last ${k}`);
    b.type = "button";
    b.addEventListener("click", () => {
      ranges.set(f.name, k);
      fill();
    });
    rng.append(b);
  }
  const now = h(
    "div",
    "now",
    h("div", "big", h("span", "p", String(total), h("small", null, `items, last ${range}`)), e),
    rng,
    bars(f, range),
  );

  const facts = h(
    "dl",
    "facts",
    h("dt", null, "Started"),
    h("dd", null, clock(f.started_at), h("span", null, ` · ${age(nowS() - f.started_at)} ago`)),
    h("dt", null, "Reads"),
    h("dd", null, `every ${dur(f.interval_s)}`, h("span", null, ` · debounce ${dur(f.debounce_s)} · cap ${f.max_items}`)),
    h("dt", null, "Delivered"),
    h("dd", null, plural(f.delivered, "item"), h("span", null, ` · ${plural(f.batch, "batch")}`)),
    f.filtered > 0 && h("dt", null, "Filtered"),
    f.filtered > 0 && h("dd", null, `${f.filtered} dropped`),
    f.dropped > 0 && h("dt", null, "Overflow"),
    f.dropped > 0 && h("dd", null, `${f.dropped} oldest dropped`),
  );

  const acts = h("span", "acts");
  const btn = (text, op, cls) => {
    const b = h("button", cls, text);
    b.type = "button";
    b.addEventListener("click", async () => {
      if (op === "feed.cancel" && !b.dataset.sure) {
        b.dataset.sure = "1";
        b.textContent = f.pending ? `Cancel, drop ${f.pending}?` : "Cancel it?";
        return;
      }
      b.disabled = true;
      try {
        await call(op, { name: f.name });
      } catch (err) {
        b.textContent = err?.message || "failed";
      }
    });
    return b;
  };
  if (f.pending && !f.paused) {
    const sn = btn("Send now", "feed.send");
    if (f.state === "waits for the turn") sn.title = "They go when the turn ends";
    acts.append(sn);
  }
  acts.append(f.paused ? btn("Resume", "feed.resume") : btn("Pause", "feed.pause"), btn("Cancel", "feed.cancel", "dz"));
  const cwd = h("span", "path", f.cwd);
  cwd.title = f.cwd;
  const ft = h("div", "ft", cwd, acts);
  return [hd, now, waits(f, range), facts, latest(f, live), source(f), ft];
}

// -- state: the rows drawn, the card open -------------------------------------
let rows = new Map(); // name -> {el, sig}
let feeds = new Map(); // name -> feed
const ranges = new Map(); // name -> the range its card shows, for this tab
let openId = null, openSig = null, openT = null, closeT = null;
let live = [];
let cardEl = null;
let call = async () => {};

function cardBox() {
  if (cardEl) return cardEl;
  cardEl = h("div", "mcard fcard");
  cardEl.id = "fcard";
  cardEl.addEventListener("pointerenter", byMouse(() => clearTimeout(closeT)));
  cardEl.addEventListener("pointerleave", byMouse(soonClose));
  document.getElementById("a2").append(cardEl);
  document.addEventListener("pointerdown", (ev) => {
    if (openId && !cardEl.contains(ev.target) && !ev.target.closest?.(".mon.feed")) closeFeedCard();
  });
  document.querySelector("#a2 .side")?.addEventListener("scroll", place);
  addEventListener("resize", place);
  return cardEl;
}

function place() {
  if (!openId) return;
  const r = rows.get(openId)?.el.getBoundingClientRect();
  const side = document.querySelector("#a2 .side")?.getBoundingClientRect();
  if (!r || !side || !r.height) return closeFeedCard();
  if (narrow.matches) {
    cardEl.style.removeProperty("left");
    cardEl.style.removeProperty("top");
    return;
  }
  const ch = cardEl.offsetHeight;
  cardEl.style.left = `${Math.max(8, side.left - CARD_W - 12)}px`;
  cardEl.style.top = `${Math.max(12, Math.min(r.top - 14, innerHeight - ch - 12))}px`;
}

function fill() {
  const f = feeds.get(openId);
  live = [];
  cardEl.replaceChildren(...card(f, live));
  openSig = rows.get(openId).sig;
  place();
}

function open(id) {
  clearTimeout(closeT);
  if (!feeds.has(id)) return;
  cardBox();
  for (const { el } of rows.values()) el.classList.toggle("open", el.dataset.id === id);
  if (openId !== id) {
    openId = id;
    fill();
  }
  place();
  cardEl.classList.add("show");
}

function soonClose() {
  clearTimeout(openT);
  clearTimeout(closeT);
  closeT = setTimeout(closeFeedCard, CLOSE_AFTER_MS);
}

// Closes the card; false when none was open, so Esc can fall through.
export function closeFeedCard() {
  clearTimeout(openT);
  if (!openId) return false;
  openId = openSig = null;
  live = [];
  cardEl.classList.remove("show");
  for (const { el } of rows.values()) el.classList.remove("open");
  return true;
}

// ``callOp(op, params)`` runs an operation on the session these feeds are on.
export function renderFeeds(box, list, callOp) {
  call = callOp;
  feeds = new Map(list.map((f) => [f.name, f]));
  const next = new Map();
  for (const f of list) {
    const sig = JSON.stringify(f);
    const had = rows.get(f.name);
    if (had && had.sig === sig && had.el.parentNode === box) next.set(f.name, had);
    else {
      const el = row(f);
      if (had) had.el.replaceWith(el);
      next.set(f.name, { el, sig });
    }
  }
  const order = [...next.values()].map((r) => r.el);
  if (order.length !== box.children.length || order.some((el, i) => box.children[i] !== el)) box.replaceChildren(...order);
  rows = next;
  if (!openId) return;
  if (!rows.has(openId)) return closeFeedCard();
  rows.get(openId).el.classList.add("open");
  if (rows.get(openId).sig !== openSig) fill();
  place();
}

// Once a second: the rows' last-item ages and the open card's countdowns.
export function tickFeeds() {
  for (const { el } of rows.values()) el.tick();
  for (const f of live) f();
}
