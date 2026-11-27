// A workflow's recap follows the card's visibility (#1190). An expanded card
// that intersects a visible browser tab is "open": the server may generate a
// recap for the run and refresh it when the run changes. A card that is
// collapsed (its transcript row while docked), offscreen, in a background
// tab, or out of the page is closed, so a run nobody reads spends nothing.
//
// The server decides what costs a call: the same state, a fold or a re-render
// is free, and one call per run is shared by every open view. This module
// only reports open, changed and closed, never the run's content.

const seen = new WeakMap(); // card -> the view it holds and what changed
let queued = false;

function visible(card) {
  if (!card.isConnected || card.dataset.expanded !== "true") return false;
  if (document.visibilityState !== "visible") return false;
  const r = card.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
}

function runOf(card) {
  return card.dataset.run || "";
}

function sync(card) {
  const s = seen.get(card);
  if (!s) return;
  const run = runOf(card);
  if (!run) return;
  if (s.run !== run) {
    // A card reused for another run: never leave the first one's view open.
    if (s.view) s.call("workflow.recap.close", { run: s.run, view: s.view }).catch(() => {});
    s.run = run;
    s.view = null;
    s.dirty = true;
  }
  const is = visible(card);
  if (is) {
    if (s.view) {
      if (s.dirty) {
        s.dirty = false;
        s.call("workflow.recap.refresh", { run, view: s.view }).then((r) => {
          // A restart forgot the token and the answer carries a new one; a
          // card that left meanwhile must not leave it open.
          if (r?.view && r.view !== s.view) {
            if (card.isConnected) s.view = r.view;
            else s.call("workflow.recap.close", { run, view: r.view }).catch(() => {});
          }
        }).catch(() => {});
      }
    } else if (!s.opening) {
      s.opening = true;
      s.call("workflow.recap.open", { run }).then((r) => {
        s.opening = false;
        if (r?.view && !card.isConnected) {
          // It left the page while the call was out: close what just opened.
          s.call("workflow.recap.close", { run, view: r.view }).catch(() => {});
        } else {
          if (r?.view) s.view = r.view;
          // It may have left the viewport while the call was out.
          sync(card);
        }
      }).catch(() => {
        s.opening = false;
      });
    }
  } else if (s.view) {
    const view = s.view;
    s.view = null;
    s.dirty = false;
    s.call("workflow.recap.close", { run, view }).catch(() => {});
  }
}

function syncAll() {
  for (const card of document.querySelectorAll(".wfcard")) sync(card);
}

function soon() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => {
    queued = false;
    syncAll();
  });
}

function release(node) {
  const card = node.nodeType === 1 ? node : null;
  if (!card) return;
  const cards = card.matches?.(".wfcard") ? [card] : [...(card.querySelectorAll?.(".wfcard") || [])];
  for (const c of cards) {
    const s = seen.get(c);
    if (!s) continue;
    if (s.view) s.call("workflow.recap.close", { run: s.run, view: s.view }).catch(() => {});
    seen.delete(c);
  }
}

// A card left the page without being hidden first: the transcript changed
// session or re-rendered, so its view closes with it.
function watchRemovals() {
  new MutationObserver((records) => {
    for (const r of records) for (const n of r.removedNodes) release(n);
  }).observe(document.body, { childList: true, subtree: true });
}
if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", watchRemovals);
} else watchRemovals();

// Scroll, resize and a tab coming back are the moments a card starts or
// stops intersecting. Scroll is captured, so a pane's own scroller counts.
document.addEventListener("scroll", soon, true);
window.addEventListener("resize", soon);
document.addEventListener("visibilitychange", soon);

// fillWorkflow calls this on every draw: the card's run may have changed, and
// a visible one asks the server for a refresh. The server never pays twice
// for the same inputs.
export function watchRecap(card, call) {
  let s = seen.get(card);
  if (!s) {
    s = { run: "", view: null, opening: false, dirty: true, call };
    seen.set(card, s);
  }
  s.call = call;
  s.dirty = true;
  sync(card);
}
