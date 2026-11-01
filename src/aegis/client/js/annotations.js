// Visual annotations for images and PDFs (#780, #913). The browser keeps
// normalized coordinates in the same comment tray as text comments. They
// survive a resize and a reload, and become JSON in the message the person
// sends.
//
// Point, area, draw, highlight, arrow and off are icons, named on hover.
// Off leaves the PDF's own scrolling and zoom in reach. A stroke and an
// arrow store normalized points; a highlight is a translucent region.

import { icon } from "./glyphs.js";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

const NS = "http://www.w3.org/2000/svg";
const rounded = (n) => Number(n.toFixed(4));
const MIN = 0.01; // a smaller drag is a click, not a shape
const STEP = 0.008; // how far the pen moves before the stroke keeps a point
const MAX_POINTS = 200;

// One line, so the glyph test can see every name. off reuses the close icon.
const TOOL_GLYPH = { point: "point", area: "region", draw: "draw", highlight: "highlight", arrow: "mark-arrow", off: "close" };
const TOOLS = [
  ["point", "Point"],
  ["area", "Area"],
  ["draw", "Draw"],
  ["highlight", "Highlight"],
  ["arrow", "Arrow"],
  ["off", "Annotate off"],
];

function svgEl(tag, attrs) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

// The arrow's head, in the same 0–1 space as the shaft. A short arrow still
// gets a head; the caller has already rejected a click.
function arrowHead(a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const s = Math.min(0.045, len * 0.45);
  const px = -uy;
  const py = ux;
  return [
    b,
    { x: b.x - ux * s + px * s * 0.55, y: b.y - uy * s + py * s * 0.55 },
    { x: b.x - ux * s - px * s * 0.55, y: b.y - uy * s - py * s * 0.55 },
  ];
}

// The person's stroke or arrow, drawn over the media. This is the mark, not
// an icon: the sprite holds the tool buttons.
export function strokeNode(at) {
  const svg = svgEl("svg", { class: "mark", viewBox: "0 0 1 1", preserveAspectRatio: "none" });
  const ink = { fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" };
  if (at.type === "arrow" && at.points?.length >= 2) {
    const [a, b] = at.points;
    svg.append(svgEl("line", { ...ink, x1: a.x, y1: a.y, x2: b.x, y2: b.y }));
    const head = arrowHead(a, b);
    svg.append(svgEl("polygon", { points: head.map((p) => `${p.x},${p.y}`).join(" "), fill: "currentColor" }));
  } else if (at.points?.length) {
    svg.append(svgEl("polyline", { ...ink, points: at.points.map((p) => `${p.x},${p.y}`).join(" ") }));
  }
  return svg;
}

// Add the drawing layer and its controls to a preview stage. A PDF's native
// viewer does not expose its current page or its text, so the person names
// the visible page before drawing.
export function annotate(stage, tray, { path, kind }) {
  if (!tray || !["image", "pdf"].includes(kind)) return null;
  const target = stage.querySelector("img, iframe");
  if (!target) return null;
  stage.classList.add("visual-annotations");
  const tools = el("div", "vatools");
  const buttons = TOOLS.map(([name, title]) => {
    const b = el("button", "btn icon");
    b.type = "button";
    b.dataset.tool = name;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.setAttribute("aria-pressed", name === "off" ? "true" : "false");
    b.append(icon(TOOL_GLYPH[name]));
    tools.append(b);
    return b;
  });
  let page = null;
  if (kind === "pdf") {
    const label = el("label", "vapage", "page ");
    page = el("input");
    page.type = "number";
    page.min = "1";
    page.value = "1";
    page.setAttribute("aria-label", "PDF page to annotate");
    page.title = "Set the page visible in the PDF viewer before drawing";
    label.append(page);
    tools.append(label);
  }
  const layer = el("div", "valayer");
  layer.setAttribute("aria-label", `Annotations on ${path}`);
  stage.append(layer, tools);
  tray.layer(layer);

  const sync = () => {
    const outer = stage.getBoundingClientRect();
    const inner = target.getBoundingClientRect();
    layer.style.left = `${inner.left - outer.left + stage.scrollLeft}px`;
    layer.style.top = `${inner.top - outer.top + stage.scrollTop}px`;
    layer.style.width = `${inner.width}px`;
    layer.style.height = `${inner.height}px`;
    tray.pins();
  };
  target.addEventListener("load", sync);
  const resize = new ResizeObserver(sync);
  resize.observe(stage);
  resize.observe(target);
  requestAnimationFrame(sync);

  let tool = "off";
  const select = (name) => {
    tool = name;
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.tool === name));
    layer.classList.toggle("active", name !== "off");
  };
  for (const b of buttons) b.addEventListener("click", () => select(b.dataset.tool));

  let start = null;
  let draft = null;
  let pts = null;
  const here = (ev) => {
    const b = layer.getBoundingClientRect();
    if (!b.width || !b.height) return null;
    return {
      x: Math.max(0, Math.min(1, (ev.clientX - b.left) / b.width)),
      y: Math.max(0, Math.min(1, (ev.clientY - b.top) / b.height)),
    };
  };
  const place = (p) => ({ x: rounded(p.x), y: rounded(p.y) });
  const remember = (p) => {
    const q = place(p);
    const prev = pts[pts.length - 1];
    if (prev && Math.hypot(q.x - prev.x, q.y - prev.y) < STEP) return;
    if (pts.length >= MAX_POINTS) pts.splice(1, 1);
    pts.push(q);
  };
  const paint = () => {
    draft?.querySelector("svg")?.remove();
    if (!pts?.length || (tool === "arrow" && pts.length < 2)) return;
    draft?.append(strokeNode({ type: tool, points: pts }));
  };
  const payload = (type, extra) => {
    const annotation = { file: path, type };
    if (page) annotation.page = Math.max(1, Number(page.value) || 1);
    Object.assign(annotation, extra);
    const where = page ? `page ${annotation.page}` : "image";
    return { annotation, where };
  };
  const clear = () => {
    start = null;
    pts = null;
    draft?.remove();
    draft = null;
  };

  layer.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || tool === "off") return;
    ev.preventDefault();
    start = here(ev);
    if (!start) return;
    if (tool === "draw" || tool === "arrow") {
      pts = [place(start)];
      draft = el("span", "vadraft draw");
      layer.append(draft);
      paint();
    } else if (tool !== "point") {
      draft = el("span", tool === "highlight" ? "vadraft highlight" : "vadraft");
      layer.append(draft);
    }
    layer.setPointerCapture(ev.pointerId);
  });
  layer.addEventListener("pointermove", (ev) => {
    if (!start) return;
    const now = here(ev);
    if (!now) return;
    if (tool === "draw") {
      remember(now);
      paint();
      return;
    }
    if (tool === "arrow") {
      pts = [place(start), place(now)];
      paint();
      return;
    }
    if (!draft) return;
    const x = Math.min(start.x, now.x);
    const y = Math.min(start.y, now.y);
    Object.assign(draft.style, {
      left: `${x * 100}%`,
      top: `${y * 100}%`,
      width: `${Math.abs(now.x - start.x) * 100}%`,
      height: `${Math.abs(now.y - start.y) * 100}%`,
    });
  });
  layer.addEventListener("pointerup", (ev) => {
    if (!start) return;
    const end = here(ev) || start;
    const from = start;
    if (tool === "draw" && pts) remember(end);
    const drawn = pts ? pts.slice() : null;
    clear();
    if (tool === "point") {
      const at = { type: "point", ...place(end), width: 0, height: 0 };
      const { annotation, where } = payload("point", { region: { x: at.x, y: at.y, width: 0, height: 0 } });
      tray.add({ locator: `${where} point`, quote: "", at, annotation });
      return;
    }
    if (tool === "draw") {
      if (!drawn || drawn.length < 2) return;
      const at = { type: "draw", points: drawn };
      const { annotation, where } = payload("draw", { points: drawn });
      tray.add({ locator: `${where} draw`, quote: "", at, annotation });
      return;
    }
    if (tool === "arrow") {
      const a = place(from);
      const b = place(end);
      if (Math.hypot(b.x - a.x, b.y - a.y) < MIN) return;
      const points = [a, b];
      const at = { type: "arrow", points };
      const { annotation, where } = payload("arrow", { points });
      tray.add({ locator: `${where} arrow`, quote: "", at, annotation });
      return;
    }
    const dx = Math.abs(end.x - from.x);
    const dy = Math.abs(end.y - from.y);
    if (dx < MIN && dy < MIN) return;
    const type = tool === "highlight" ? "highlight" : "region";
    const box = {
      x: rounded(Math.min(from.x, end.x)),
      y: rounded(Math.min(from.y, end.y)),
      width: rounded(dx),
      height: rounded(dy),
    };
    const at = { type, ...box };
    const { annotation, where } = payload(type, { region: box });
    tray.add({ locator: `${where} ${type}`, quote: "", at, annotation });
  });
  layer.addEventListener("pointercancel", clear);
  return { sync, destroy: () => resize.disconnect() };
}
