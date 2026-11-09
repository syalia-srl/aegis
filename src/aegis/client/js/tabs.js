// The tab bar. The set of tabs is the server's open sessions, the same in
// every browser; their order is this browser's own, kept in localStorage as a
// list of log ids. Gone ids drop out, new sessions go at the end. A group (a
// session and those it started, groups.js) is drawn as one block headed by its
// oldest member, which can fold it to one tab (#586).

import { labelsFor, stateLabel } from "./labels.js";
import { iconText, PANE_ICON } from "./glyphs.js";
import { closeOnMiddle, markNode } from "./fleet.js";
import { blocksOf, hueOf } from "./groups.js";
import { shownOwner } from "./me.js";

export class TabOrder {
  constructor(key = "aegis.tabs") {
    this.key = key;
  }

  load() {
    try {
      const v = JSON.parse(localStorage.getItem(this.key) || "[]");
      return Array.isArray(v) ? v : [];
    } catch {
      return [];
    }
  }

  save(ids) {
    localStorage.setItem(this.key, JSON.stringify(ids));
  }

  // `save: false` while the page draws the device's copy of the Fleet: a
  // session it did not keep yet is not one the person closed, and dropping it
  // from the order here would move every tab after a reload that beat the
  // Fleet's next save.
  arrange(openIds, save = true) {
    const open = new Set(openIds);
    const kept = this.load().filter((id) => open.has(id));
    const known = new Set(kept);
    const ids = [...kept, ...openIds.filter((id) => !known.has(id))];
    if (save) this.save(ids);
    return ids;
  }

  // `moving` (keys, in their order) to just before `beforeId`, or the end.
  move(moving, beforeId) {
    const ids = this.load().filter((x) => !moving.includes(x));
    const at = beforeId ? ids.indexOf(beforeId) : -1;
    ids.splice(at < 0 ? ids.length : at, 0, ...moving);
    this.save(ids);
  }
}

const URGENT = ["needs_you", "error", "review", "working", "waiting"];

// `actions.here` names this server; `actions.folds` is the set of folded group
// keys, `actions.onFold(key)` toggles one and `actions.saveFolds()` keeps the
// set. A folded group whose hidden member is the one in view unfolds.
export function renderTabs(list, metas, focusId, actions) {
  const t = actions.t || labelsFor("en");
  list.replaceChildren(
    ...blocksOf(metas, actions.here || "").map((b) => {
      const box = document.createElement("div");
      box.className = "tblock";
      box.dataset.group = b.key;
      // Every member's key, in tab order, folded ones too: what a drag moves.
      box.dataset.keys = JSON.stringify(metas.filter((m) => b.members.includes(m)).map((m) => m.key));
      if (b.members.length < 2) {
        box.append(tab(b.head, focusId, actions, "solo"));
        return box;
      }
      const hidden = b.members.slice(1);
      if (actions.folds?.has(b.key) && hidden.some((m) => m.key === focusId)) {
        actions.folds.delete(b.key);
        actions.saveFolds();
      }
      const folded = !!actions.folds?.has(b.key);
      box.classList.add("grp");
      box.style.setProperty("--g", `var(--group-${hueOf(b.key)})`);
      const mark = folded ? URGENT.find((a) => b.members.some((m) => m.mark === a)) : null;
      const head = tab(mark ? { ...b.head, mark } : b.head, focusId, actions, "head");
      const cnt = document.createElement("span");
      cnt.className = "cnt";
      cnt.textContent = folded ? `+${hidden.length}` : "−";
      cnt.title = t(folded ? "tabs.unfold" : "tabs.fold");
      cnt.classList.toggle("alert", folded && hidden.some((m) => m.mark === "needs_you"));
      cnt.addEventListener("click", (ev) => {
        ev.stopPropagation();
        actions.onFold(b.key);
      });
      head.append(cnt);
      box.append(head, ...(folded ? [] : hidden.map((m) => tab(m, focusId, actions, "member"))));
      return box;
    }),
  );
}

// One session's tab redrawn where it stands; the others keep their nodes. A
// tab inside a group is not patched (false): its head and count depend on its
// members, so the caller redraws the bar.
export function patchTab(list, m, focusId, actions) {
  const old = list.querySelector(`.tab[data-id="${CSS.escape(m.key)}"]`);
  if (!old) return true;
  if (old.closest(".tblock.grp")) return false;
  old.replaceWith(tab(m, focusId, actions, "solo"));
  return true;
}

// What is being dragged: one member inside its group, or a whole block.
let dragging = null;
// The tab whose pointer went down and has not been released yet: a drag starts
// a moment later, and a strip that redraws in between loses the node the
// browser was about to drag. Redraws wait from pointerdown until dragend (or
// pointerup, for a plain click).
let holding = null;

// Whether a drag owns the strip right now. A part that draws tabs asks this
// before rebuilding them: replacing the node under the pointer cancels the
// drag, and a sessions patch may arrive at any moment.
export const draggingTab = () => dragging !== null || holding !== null;

function releaseHold() {
  const node = holding;
  holding = null;
  document.removeEventListener("pointerup", releaseHold);
  document.removeEventListener("pointercancel", releaseHold);
  node?.dispatchEvent(new CustomEvent("aegis-tabdrag-end", { bubbles: true }));
}

function holdTab(node) {
  holding = node;
  document.addEventListener("pointerup", releaseHold);
  document.addEventListener("pointercancel", releaseHold);
}

// Where a drop on tab `t` at `x` puts what is dragged: the key it goes before
// (null: the end), or undefined when it may not go there.
function dropBefore(t, x) {
  const after = x > t.getBoundingClientRect().left + t.getBoundingClientRect().width / 2;
  const box = t.closest(".tblock");
  if (dragging.member) {
    if (box.dataset.group !== dragging.group || !t.classList.contains("member")) return undefined;
    if (dragging.keys.includes(t.dataset.id)) return undefined;
    if (!after) return t.dataset.id;
    const next = t.nextElementSibling?.dataset.id;
    return next ?? firstKey(box.nextElementSibling);
  }
  if (box.dataset.group === dragging.group) return undefined;
  return after ? firstKey(box.nextElementSibling) : firstKey(box);
}
const firstKey = (box) => (box ? JSON.parse(box.dataset.keys)[0] : null);

// Where Alt+Shift+[ or ] moves tab `key` one place (`d` is -1 or 1), by the
// rules of a drag: a member within its group, a head or a lone tab with its
// whole block. The keys to move and the key they go before (null: the end),
// as TabOrder.move takes them, or null at an edge.
export function stepMove(list, key, d) {
  const t = list.querySelector(`.tab[data-id="${CSS.escape(key)}"]`);
  if (!t) return null;
  const box = t.closest(".tblock");
  if (t.classList.contains("member")) {
    const side = d < 0 ? t.previousElementSibling : t.nextElementSibling;
    if (!side?.classList.contains("member")) return null;
    if (d < 0) return [[key], side.dataset.id];
    return [[key], side.nextElementSibling?.dataset.id ?? firstKey(box.nextElementSibling)];
  }
  const keys = JSON.parse(box.dataset.keys);
  if (d < 0) return box.previousElementSibling ? [keys, firstKey(box.previousElementSibling)] : null;
  return box.nextElementSibling ? [keys, firstKey(box.nextElementSibling.nextElementSibling)] : null;
}

function tab(m, focusId, { onFocus, onMove, onClose, t = labelsFor("en") }, role) {
  const node = document.createElement("div");
  node.className = `tab ${role}${m.key === focusId ? " on" : ""}${m.state === "asleep" ? " asleep" : ""}${m.off ? " off" : ""}`;
  node.classList.toggle("blink", !!m.blink);
  node.draggable = true;
  node.dataset.id = m.key;
  const said = t("tabs.said", { handle: m.handle, title: m.title || t("sidebar.untitled"), state: stateLabel(t, m.attention) || m.state });
  const mark = markNode(m);
  const term = m.kind === "term" || m.kind === "browser";
  if (term) {
    // A terminal is named by its name; its command is in the title.
    node.title = said;
    const name = Object.assign(document.createElement("span"), { className: "tname" });
    name.replaceChildren(...iconText(PANE_ICON[m.kind === "term" ? "term" : "browser"], m.name));
    node.append(mark, name);
  } else {
    // The whole handle, and the title under it cut to the handle's width (#586).
    node.setAttribute("aria-label", said);
    const lines = Object.assign(document.createElement("span"), { className: "l2" });
    lines.append(
      Object.assign(document.createElement("span"), { className: "hn", textContent: m.handle }),
      Object.assign(document.createElement("span"), { className: "tt", textContent: m.title || "" }),
    );
    node.append(mark, lines);
  }
  // Whose it is, when it is not this browser's (tokens.py).
  const owner = shownOwner(m);
  if (owner) node.append(Object.assign(document.createElement("span"), { className: "who", textContent: owner }));
  // A session on a linked server carries that server's name (links.py).
  if (m.server) node.append(Object.assign(document.createElement("span"), { className: "where", textContent: m.server }));
  node.addEventListener("click", () => onFocus(m.key));
  closeOnMiddle(node, () => onClose(m.key));
  node.addEventListener("pointerdown", (ev) => {
    if (ev.button === 0) holdTab(node);
  });
  node.addEventListener("dragstart", (ev) => {
    ev.dataTransfer.setData("text/aegis-tab", m.key);
    ev.dataTransfer.effectAllowed = "move";
    const box = node.closest(".tblock");
    const member = role === "member";
    dragging = { group: box.dataset.group, member, keys: member ? [m.key] : JSON.parse(box.dataset.keys) };
    (member ? node : box).classList.add("dragging");
  });
  node.addEventListener("dragend", () => {
    node.closest(".tblock")?.classList.remove("dragging");
    node.classList.remove("dragging");
    dragging = null;
    if (holding === node) holding = null;
    document.removeEventListener("pointerup", releaseHold);
    document.removeEventListener("pointercancel", releaseHold);
    // A strip that was asked to redraw during the drag waited for this.
    node.dispatchEvent(new CustomEvent("aegis-tabdrag-end", { bubbles: true }));
  });
  node.addEventListener("dragover", (ev) => {
    if (!dragging || !ev.dataTransfer.types.includes("text/aegis-tab")) return;
    if (dropBefore(node, ev.clientX) === undefined) return node.classList.add("nodrop");
    ev.preventDefault();
    node.classList.add("drop");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop", "nodrop"));
  node.addEventListener("drop", (ev) => {
    ev.preventDefault();
    node.classList.remove("drop", "nodrop");
    const before = dragging && dropBefore(node, ev.clientX);
    if (before !== undefined && dragging) onMove(dragging.keys, before);
  });
  return node;
}
