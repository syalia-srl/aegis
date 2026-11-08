// aegis's own confirm: a question over the dimmed page and two buttons, drawn
// like the ? list. The browser's own confirm dialog looks foreign in the
// installed app and blocks the page (tests/test_client_rules.py bans it). Enter
// on the focused OK is the button's own; Esc reaches cancelAsk() through the
// escape action in keys.js; a tap outside cancels.
//
// A dialog is drawn inside the mount that asked (a part passes its root, or
// the caller's focus finds it), so two mounts never share one panel: closing
// the full layout's question cannot hide a chat's.

import { rootOf } from "./root.js";

const boxes = new WeakMap(); // root -> {dialog, chooser}
let open = null; // the pending answer's resolve
let openRoot = null; // where that answer's dialog was drawn
let picked = null; // the pending choice's resolve
let pickedRoot = null;

function parts(root) {
  let b = boxes.get(root);
  if (!b) boxes.set(root, (b = {}));
  return b;
}

function build(root, id, html) {
  const d = document.createElement("div");
  d.className = "dialog";
  d.id = id;
  d.hidden = true;
  d.innerHTML = html;
  root.append(d);
  return d;
}

function box(root) {
  const b = parts(root);
  if (!b.dialog) {
    b.dialog = build(
      root,
      "dialog",
      '<div class="panel" role="dialog" aria-modal="true"><p class="q"></p>' +
        '<div class="acts"><button class="btn cancel"></button><button class="btn primary ok"></button></div></div>',
    );
    const d = b.dialog;
    d.addEventListener("click", (ev) => ev.target === d && answer(false, root));
    d.querySelector(".ok").addEventListener("click", () => answer(true, root));
    d.querySelector(".cancel").addEventListener("click", () => answer(false, root));
  }
  return b.dialog;
}

function answer(yes, root = openRoot) {
  const resolve = open;
  open = null;
  openRoot = null;
  if (root) box(root).hidden = true;
  resolve?.(yes);
}

export function ask(question, { ok = "OK", cancel = "Cancel", root = null } = {}) {
  const at = root || rootOf(document.activeElement);
  if (open) answer(false);
  const d = box(at);
  d.querySelector(".q").textContent = question;
  d.querySelector(".ok").textContent = ok;
  d.querySelector(".cancel").textContent = cancel;
  d.hidden = false;
  openRoot = at;
  d.querySelector(".ok").focus();
  return new Promise((resolve) => (open = resolve));
}

export function cancelAsk() {
  if (picked) {
    pick(null);
    return true;
  }
  if (!open) return false;
  answer(false);
  return true;
}

// The same panel with a body and any number of buttons: resolves the pressed
// button's value, or null on Esc, a tap outside or another dialog. The
// untested-harness question (app.js untestedAsk) is one.
function chooser(root) {
  const b = parts(root);
  if (!b.chooser) {
    b.chooser = build(
      root,
      "chooser",
      '<div class="panel wide" role="dialog" aria-modal="true"><p class="q"></p><div class="b"></div><div class="acts"></div></div>',
    );
    const d = b.chooser;
    d.addEventListener("click", (ev) => ev.target === d && pick(null, root));
  }
  return b.chooser;
}

function pick(value, root = pickedRoot) {
  const resolve = picked;
  picked = null;
  pickedRoot = null;
  if (root) chooser(root).hidden = true;
  resolve?.(value);
}

export function choose(title, body, buttons, { root = null } = {}) {
  const at = root || rootOf(document.activeElement);
  if (picked) pick(null);
  if (open) answer(false);
  const d = chooser(at);
  d.querySelector(".q").textContent = title;
  d.querySelector(".b").replaceChildren(body);
  const acts = d.querySelector(".acts");
  acts.replaceChildren(
    ...buttons.map((b) => {
      const el = document.createElement("button");
      el.className = b.primary ? "btn primary" : "btn";
      el.textContent = b.label;
      el.dataset.value = b.value;
      el.addEventListener("click", () => pick(b.value, at));
      return el;
    }),
  );
  d.hidden = false;
  pickedRoot = at;
  (acts.querySelector(".primary") || acts.lastChild)?.focus();
  return new Promise((resolve) => (picked = resolve));
}
