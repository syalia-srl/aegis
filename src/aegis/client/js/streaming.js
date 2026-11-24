// Only live reply patches fade. The wire data stays whole, as do copy and
// search. Absolute start times survive row replacement, so the next token
// cannot restart a letter's fade. At most 120 graphemes animate, and the last
// one starts within 180 ms: a burst catches up instead of building a queue.
const states = new WeakMap();
const segments = new Intl.Segmenter(undefined, { granularity: "grapheme" });
const FADE = 120;
const AHEAD = 180;
const LIMIT = 120;

function textNodes(row) {
  const body = row?.querySelector(":scope > .body");
  if (!body) return [];
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      if (n.parentElement.closest("button, [data-chrome], .fh")) return NodeFilter.FILTER_REJECT;
      // markdown-it inserts newlines between block elements. They are layout
      // whitespace, not part of the growing paragraph's visible prefix.
      if (!n.data.trim() && n.parentElement.matches(".md, ul, ol, table, thead, tbody, tr, blockquote")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  return nodes;
}

export function fadeReply(old, row) {
  if (document.hidden || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const nodes = textNodes(row);
  const text = nodes.map((n) => n.data).join("");
  const prev = states.get(old) || { text: textNodes(old).map((n) => n.data).join(""), pending: [] };
  // Closing Markdown can change earlier visible text (e.g. removing **).
  // Draw that reconciliation immediately rather than replaying old words.
  if (!text.startsWith(prev.text)) return;
  const now = performance.now();
  const pending = prev.pending.filter((p) => p.at + FADE > now);
  const added = [...segments.segment(text.slice(prev.text.length))].slice(-LIMIT);
  const start = Math.max(now, pending.at(-1)?.at || now);
  const step = Math.min(18, (now + AHEAD - start) / Math.max(added.length, 1));
  added.forEach((s, i) => pending.push({ offset: prev.text.length + s.index, size: s.segment.length, at: start + step * i }));
  const tail = pending.slice(-LIMIT);
  states.set(row, { text, pending: tail });
  let offset = 0;
  let i = 0;
  for (const n of nodes) {
    const end = offset + n.length;
    const frag = document.createDocumentFragment();
    let from = 0;
    while (i < tail.length && tail[i].offset < end) {
      const p = tail[i++];
      const at = p.offset - offset;
      frag.append(n.data.slice(from, at));
      const span = document.createElement("span");
      span.className = "stream-char";
      span.style.setProperty("--stream-delay", `${p.at - now}ms`);
      span.textContent = n.data.slice(at, at + p.size);
      frag.append(span);
      from = at + p.size;
    }
    if (from) {
      frag.append(n.data.slice(from));
      n.replaceWith(frag);
    }
    offset = end;
  }
}
