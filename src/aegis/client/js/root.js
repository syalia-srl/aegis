// The mount a node belongs to, for the modules that draw outside a part's
// tree (a toast, the context menu, a dialog, the glyph sprite). A part passes
// its mount's root when it has one; anything else is found by walking up from
// the node that caused the drawing.
//
// The fallback is the standalone page's own root (#a2), so every module that
// still appends to the page keeps working until the slice that moves it gives
// it a root (b1–b8). It is not an id lookup for a part's own nodes: the .aegis
// walk above it is what finds a mount.
export function rootOf(node) {
  return (
    node?.closest?.(".aegis") ||
    document.getElementById("a2") ||
    document.body
  );
}
