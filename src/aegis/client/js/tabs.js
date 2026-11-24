// The tab bar. The set of tabs is the server's open sessions, the same in
// every browser; their order is this browser's own, kept in localStorage as a
// list of log ids. Gone ids drop out, new sessions go at the end. A group (a
// named record over sessions, groups.js) is drawn as one block headed by the
// group's name, which can fold it to one tab (#1039). A folded block draws
// only the member in view beside it (#1076).

import { labelsFor, stateLabel } from "./labels.js";
import { iconText, PANE_ICON } from "./glyphs.js";
import { closeOnMiddle, markNode } from "./fleet.js";
import { blocksOf, groupKey, hueOf, poppedOut } from "./groups.js";
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

// `actions.here` names this server; `actions.folds` is the set of folded group
// keys, `actions.onFold(key)` toggles one and `actions.saveFolds()` keeps the
// set. `actions.onGroup(key)` opens a group's page, and `actions.group` is the
// key of the group in view, if one is.
export function renderTabs(list, metas, focusId, actions) {
  const t = actions.t || labelsFor("en");
  cancelTabDrag(list);
  wireDrag(list, actions);
  list.replaceChildren(
    ...blocksOf(metas, actions.here || "").flatMap((b) => {
      const box = document.createElement("div");
      box.className = "tblock";
      box.dataset.group = b.key;
      // Every member's key, in tab order, folded ones too: what a drag moves.
      const members = b.head.kind === "group" ? b.members : [b.head, ...b.members];
      box.dataset.keys = JSON.stringify(members.map((m) => m.key));
      if (b.head.kind !== "group") {
        box.append(tab(b.head, focusId, actions, "solo"));
        return [box];
      }
      box.classList.add("grp");
      box.style.setProperty("--g", `var(--group-${hueOf(b.key)})`);
      const folded = !!actions.folds?.has(b.key);
      const head = groupTab(b, folded, actions);
      // A quiet member has no tab but still counts in the group's +N.
      const here = actions.here || "";
      const count = (actions.all || []).filter((m) => groupKey(m, here) === b.key).length || b.members.length;
      const cnt = document.createElement("span");
      cnt.className = "cnt";
      cnt.textContent = folded ? `+${count}` : "−";
      cnt.title = t(folded ? "tabs.unfold" : "tabs.fold");
      cnt.classList.toggle("alert", folded && b.members.some((m) => m.mark === "needs_you"));
      cnt.addEventListener("click", (ev) => {
        ev.stopPropagation();
        actions.onFold(b.key);
      });
      head.append(cnt);
      box.append(head, ...(folded ? [] : b.members.map((m) => tab(m, focusId, actions, "member"))));
      // The member in view, drawn as its own tab right after the folded block,
      // in the group's colour (#1076).
      const out = poppedOut(b, folded, focusId).map((m) => {
        const beside = document.createElement("div");
        beside.className = "tblock out";
        beside.dataset.group = b.key;
        beside.dataset.keys = JSON.stringify([m.key]);
        beside.style.setProperty("--g", `var(--group-${hueOf(b.key)})`);
        beside.append(tab(m, focusId, actions, "out"));
        return beside;
      });
      return [box, ...out];
    }),
  );
}

// One session's tab redrawn where it stands; the others keep their nodes. A
// tab inside a group, or popped out of one, is not patched (false): the head,
// the count and the pop-out depend on its members, so the caller redraws the bar.
export function patchTab(list, m, focusId, actions) {
  const old = list.querySelector(`.tab[data-id="${CSS.escape(m.key)}"]`);
  if (!old) {
    // A folded member has no tab of its own, but its mark changes the group's tab.
    const key = groupKey(m, actions.here || "");
    return !list.querySelector(`.tblock.grp[data-group="${CSS.escape(key)}"]`);
  }
  // A tab that joined a group its block did not name changes the bar's shape:
  // the caller redraws it rather than patching one tab (tabs.js renderTabs).
  if (old.closest(".tblock.grp, .tblock.out")) return false;
  const box = old.closest(".tblock");
  if (box && box.dataset.group !== groupKey(m, actions.here || "")) return false;
  if (old === tabDrags.get(list)?.source) cancelTabDrag(list);
  old.replaceWith(tab(m, focusId, actions, "solo"));
  return true;
}

// A group's own tab: its name, and while the block is folded its most urgent
// mark. It carries the block, not a session, and a click opens the group's page;
// a double click, or Rename group, edits the name in place (#1039).
function groupTab(b, folded, actions) {
  const { onGroup, onRenameStart, renaming, t = labelsFor("en") } = actions;
  const node = document.createElement("div");
  node.className = `tab named${actions.group === b.key ? " on" : ""}`;
  node.dataset.group = b.key;
  node.title = t("tabs.groupTitle", { name: b.head.name });
  node.setAttribute("aria-label", node.title);
  // The generated title (#1047) says what the group works on.
  if (b.head.title) node.title += `\n${b.head.title}`;
  if (renaming === b.key) {
    const input = document.createElement("input");
    input.className = "ren";
    input.type = "text";
    input.maxLength = 60;
    input.value = b.head.name;
    input.setAttribute("aria-label", t("tabs.renameGroup"));
    let done = false;
    const save = () => {
      if (done) return;
      done = true;
      const name = input.value.trim();
      if (name && name !== b.head.name) actions.onRenameSave?.(b.key, name);
      else actions.onRenameCancel?.();
    };
    const cancel = () => {
      if (done) return;
      done = true;
      actions.onRenameCancel?.();
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        save();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        cancel();
      }
    });
    // A click elsewhere saves, as the menu's own rename does; Escape cancels
    // before the blur can save.
    input.addEventListener("blur", save);
    input.addEventListener("click", (ev) => ev.stopPropagation());
    node.draggable = false;
    node.append(input);
    setTimeout(() => {
      input.focus();
      input.select();
    }, 0);
    return node;
  }
  node.draggable = true;
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.addEventListener("keydown", (ev) => {
    if (ev.target !== node || (ev.key !== "Enter" && ev.key !== " ")) return;
    ev.preventDefault();
    onGroup?.(b.key);
  });
  const name = document.createElement("span");
  name.className = "hn";
  name.textContent = b.head.name;
  node.append(...(folded ? [markNode({ mark: b.head.mark })] : []), name);
  // Two clicks open the page; a double click is a rename, so the page waits
  // out the browser's double-click window (#1039).
  let clickTimer = null;
  node.addEventListener("click", (ev) => {
    if (ev.target.closest(".cnt")) return;
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => onGroup?.(b.key), 180);
  });
  node.addEventListener("dblclick", (ev) => {
    ev.preventDefault();
    clearTimeout(clickTimer);
    onRenameStart?.(b.key);
  });
  return node;
}

const tabDrags = new WeakMap();
const TAB_DRAG_TYPE = "text/aegis-tab";
let dragSerial = 0;

function clearFeedback(list, drag) {
  drag.target?.classList.remove("drop", "drop-after", "nodrop");
  drag.target = null;
  list.classList.remove("drop-gap");
  list.style.removeProperty("--drop-gap-y");
}

function stopScrolling(drag) {
  cancelAnimationFrame(drag.frame);
  drag.frame = 0;
}

export function cancelTabDrag(list) {
  const drag = tabDrags.get(list);
  if (!drag) return;
  drag.source.removeEventListener("dragend", drag.end);
  drag.mark.classList.remove("dragging");
  clearFeedback(list, drag);
  stopScrolling(drag);
  tabDrags.delete(list);
}

function wireDrag(list, actions) {
  const target = (node) => node === list ? list : node.closest(".tab");
  const here = () => actions.here || "";
  const vertical = () => list.classList.contains("vertical");
  const position = (ev) => vertical() ? ev.clientY : ev.clientX;

  list.ondragstart = (ev) => {
    const source = ev.target.closest(".tab[draggable=true]");
    if (!source || !ev.dataTransfer) return;
    cancelTabDrag(list);
    const box = source.closest(".tblock");
    const block = source.classList.contains("named");
    const group = box.dataset.group;
    const mark = block ? box : source;
    const serial = String(++dragSerial);
    const end = () => cancelTabDrag(list);
    tabDrags.set(list, {
      serial, source, mark, end, group, block, far: !hereKey(group, here()),
      keys: block ? JSON.parse(box.dataset.keys) : [source.dataset.id],
      target: null, frame: 0,
    });
    ev.dataTransfer.setData(TAB_DRAG_TYPE, serial);
    ev.dataTransfer.effectAllowed = "move";
    mark.classList.add("dragging");
    source.addEventListener("dragend", end);
  };

  list.ondragover = (ev) => {
    const drag = tabDrags.get(list);
    if (!drag || !ev.dataTransfer?.types.includes(TAB_DRAG_TYPE)) return;
    const isVertical = vertical();
    const at = position(ev);
    if (paint(ev.target, ev.clientX, ev.clientY)) ev.preventDefault();
    scrollAtEdge(ev.clientX, ev.clientY);

    function paint(node, x, y) {
      clearFeedback(list, drag);
      if (!node || !list.contains(node)) return;
      const t = target(node);
      if (!t) return;
      const action = dropAction(drag, t, isVertical ? y : x, here(), isVertical);
      if (t === list) {
        if (action) {
          list.classList.add("drop-gap");
          if (isVertical) list.style.setProperty("--drop-gap-y", `${y - list.getBoundingClientRect().top}px`);
        }
      } else {
        drag.target = t;
        t.classList.add(action ? "drop" : "nodrop");
        if (action?.after) t.classList.add("drop-after");
      }
      return action;
    }

    function scrollAtEdge(x, y) {
      const bounds = list.getBoundingClientRect();
      const start = isVertical ? bounds.top : bounds.left;
      const end = isVertical ? bounds.bottom : bounds.right;
      const edge = Math.min(24, (end - start) / 3);
      const delta = at < start + edge ? -8 : at > end - edge ? 8 : 0;
      const scroll = isVertical ? "scrollTop" : "scrollLeft";
      stopScrolling(drag);
      if (!delta) return;
      const step = () => {
        const before = list[scroll];
        list[scroll] += delta;
        if (list[scroll] === before) {
          drag.frame = 0;
          return;
        }
        paint(document.elementFromPoint(x, y), x, y);
        drag.frame = requestAnimationFrame(step);
      };
      drag.frame = requestAnimationFrame(step);
    }
  };

  list.ondragleave = (ev) => {
    const drag = tabDrags.get(list);
    if (!drag) return;
    // Chromium can omit relatedTarget as a drag crosses a row's children.
    const next = ev.relatedTarget || document.elementFromPoint(ev.clientX, ev.clientY);
    if (list.contains(next)) return;
    clearFeedback(list, drag);
    stopScrolling(drag);
  };

  list.ondrop = (ev) => {
    const drag = tabDrags.get(list);
    if (!drag || ev.dataTransfer?.getData(TAB_DRAG_TYPE) !== drag.serial) return;
    ev.preventDefault();
    const t = target(ev.target);
    const action = t && dropAction(drag, t, position(ev), here(), vertical());
    cancelTabDrag(list);
    if (action?.move) actions.onMove?.(action.move, action.before);
    else if (action?.join) actions.onJoin?.(action.join, action.group);
    else if (action?.leave) actions.onLeave?.(action.leave);
  };
}

// A block's key on this server, or null for one on a linked server.
function hereKey(key, here) {
  return here && key.endsWith(`@${here}`) ? key.slice(0, -here.length - 1) : null;
}

// A block that names a group rather than a lone session.
const namesGroup = (key) => key.split("@")[0].startsWith("grp-");

// What dropping on element `t` at an axis position does (#1039): a move within the block, a
// join of another block (or of a lone tab, as a new group), a leave on the gap
// between blocks, or undefined where the tab may not go.
function dropAction(drag, t, position, here, vertical) {
  const box = t.closest(".tblock");
  if (!box) {
    // The gap between blocks: a member of a group leaves it; a whole block or
    // a lone tab has nowhere to go.
    if (drag.block || drag.far || !namesGroup(drag.group || "")) return undefined;
    return hereKey(drag.group, here) ? { leave: drag.keys } : undefined;
  }
  const bounds = t.getBoundingClientRect();
  const middle = vertical ? bounds.top + bounds.height / 2 : bounds.left + bounds.width / 2;
  const after = position > middle;
  const key = box.dataset.group;
  const local = hereKey(key, here);
  if (drag.block) {
    if (key === drag.group || !local) return undefined;
    return { move: drag.keys, after, before: firstKey(after ? box.nextElementSibling : box) };
  }
  if (key === drag.group) {
    // Within its own block a tab reorders; the head is not a place to move to.
    if (!t.classList.contains("member") || drag.keys.includes(t.dataset.id)) return undefined;
    return {
      move: drag.keys,
      after,
      before: after ? (t.nextElementSibling?.dataset.id ?? firstKey(box.nextElementSibling)) : t.dataset.id,
    };
  }
  // Another block: both ends must be this server's (links.py, #1039).
  if (drag.far || !local) return undefined;
  const head = box.querySelector(".tab.named");
  if (head) return { join: drag.keys, group: hereKey(head.dataset.group, here) };
  const target = box.querySelector(".tab[data-id]")?.dataset.id;
  if (!target) return undefined;
  return { join: [target, ...drag.keys.filter((k) => k !== target)], group: "new" };
}

const firstKey = (box) => (box ? JSON.parse(box.dataset.keys)[0] : null);

// Where Alt+Shift+[ or ] moves tab `key` one place (`d` is -1 or 1), by the
// rules of a drag: a member within its group, or a lone tab with its whole
// block. The keys to move and the key they go before (null: the end),
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

function tab(m, focusId, actions, role) {
  const { onFocus, onClose, t = labelsFor("en") } = actions;
  const node = document.createElement("button");
  node.className = `tab ${role}${m.key === focusId ? " on" : ""}${m.state === "asleep" ? " asleep" : ""}${m.off ? " off" : ""}`;
  node.classList.toggle("blink", !!m.blink);
  node.draggable = true;
  node.type = "button";
  node.dataset.id = m.key;
  const state = stateLabel(t, m.attention) || stateLabel(t, m.state) || m.state;
  const mark = markNode(m);
  const term = m.kind === "term" || m.kind === "browser";
  if (term) {
    // A terminal is named by its name; its command is in the title.
    node.title = t("tabs.said", { handle: m.handle, title: m.title || t("sidebar.untitled"), state });
    let status;
    if (m.kind === "term") {
      if (m.mark === "running") status = t("fleet.termRunning");
      else if (m.state === "exited") status = t("fleet.termExited", { code: m.code });
      else status = t("fleet.termEnded", { reason: m.reason || "" });
    } else if (m.mark === "person") status = t("fleet.termYouHold");
    else if (m.mark === "agent") status = t("fleet.termAgentHolds");
    else status = t("fleet.termStopped", { reason: m.reason || "" });
    node.setAttribute("aria-label", t(`tabs.said.${m.kind}`, { name: m.name, state: status }));
    const name = Object.assign(document.createElement("span"), { className: "tname" });
    name.replaceChildren(...iconText(PANE_ICON[m.kind === "term" ? "term" : "browser"], m.name));
    node.append(mark, name);
  } else {
    // The whole handle, and the title under it cut to the handle's width (#586).
    node.setAttribute("aria-label", t("tabs.said", { handle: m.handle, title: m.title || t("sidebar.untitled"), state }));
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
  return node;
}
