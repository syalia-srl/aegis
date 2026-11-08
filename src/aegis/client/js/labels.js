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
