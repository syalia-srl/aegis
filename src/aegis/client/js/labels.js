// The client's strings, by key. en and es ship with aegis; a host picks one
// with lang and replaces single entries with labels. A key the chosen table
// does not hold falls back to English and is logged by name once, so a missing
// translation is visible in the console without breaking the page.
//
// Interpolation is {name}, deliberately not ${} and not %s: a table stays
// JSON-compatible (tests/test_labels.py parses both files) and a translator
// can move the variable within the sentence.

import en from "./labels/en.js";
import es from "./labels/es.js";

const TABLES = { en, es };
const warned = new Set();

// The attributes a key can fill on a part's own markup: the words, and the
// three attributes that hold a label instead of text. A part keeps its markup
// in English and names a key beside it, so the table is the only place a
// translator edits.
const T_ATTRS = [
  ["data-t", (n, v) => (n.textContent = v)],
  ["data-t-title", (n, v) => (n.title = v)],
  ["data-t-aria", (n, v) => n.setAttribute("aria-label", v)],
  ["data-t-placeholder", (n, v) => n.setAttribute("placeholder", v)],
];

// The word a session's attention is called by: the states the server names,
// in the mount's language. An unknown state has no key, and the caller falls
// back to the state itself.
const STATE_KEYS = { working: "state.working", needs_you: "state.needsYou", error: "state.error", review: "state.review", waiting: "state.waiting", done: "state.done" };
export const stateLabel = (t, name) => (name in STATE_KEYS ? t(STATE_KEYS[name]) : "");

// Fill every `data-t` in one subtree, once, before anything appends an icon or
// a count to a node whose text it would wipe.
export function applyLabels(root, t) {
  for (const [attr, set] of T_ATTRS) {
    for (const n of root.querySelectorAll(`[${attr}]`)) set(n, t(n.getAttribute(attr)));
  }
}

export function labelsFor(lang = "en", overrides = {}) {
  const known = lang in TABLES;
  const table = known ? TABLES[lang] : en;
  return function t(key, vars) {
    if (!(key in table) && !warned.has(key)) {
      warned.add(key);
      console.warn(`aegis: no ${known ? lang : "en"} label for ${key}`);
    }
    const s = overrides[key] ?? table[key] ?? en[key];
    if (s === undefined) return key; // absent everywhere: the key itself
    if (!vars) return s;
    return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`));
  };
}
