// Visual annotations for images and PDFs (#780, #913). The browser keeps
// normalized coordinates in the same comment tray as text comments. They
// survive a resize and a reload, and become JSON in the message the person
// sends.
//
// Point, area, draw, highlight, arrow and off are icons, named on hover.
// A wheel still scrolls a PDF while a tool is on: the notch that lands on
// the layer steps it aside, and the notches after it reach the viewer.
// Annotate off leaves the viewer's zoom and page controls in reach. A stroke
// and an arrow store normalized points; a highlight is a translucent region.

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

// The words of one PDF page, from the viewer's text layer. pdf.js joins a line
// into one span; a range over each word is that word's real box.
function pageWords(pageEl) {
  const box = pageEl.getBoundingClientRect();
  if (!box.width || !box.height) return [];
  const out = [];
  const word = /\S+/g;
  for (const span of pageEl.querySelectorAll(".textLayer span")) {
    for (const node of span.childNodes) {
      if (node.nodeType !== Node.TEXT_NODE) continue;
      const source = node.textContent ?? "";
      word.lastIndex = 0;
      let match;
      while ((match = word.exec(source))) {
        const range = span.ownerDocument.createRange();
        range.setStart(node, match.index);
        range.setEnd(node, match.index + match[0].length);
        const rect = range.getBoundingClientRect();
        range.detach();
        if (rect.width < 0.5 || rect.height < 0.5) continue;
        out.push({
          text: match[0],
          box: {
            x: (rect.left - box.left) / box.width,
            y: (rect.top - box.top) / box.height,
            w: rect.width / box.width,
            h: rect.height / box.height,
          },
        });
      }
    }
  }
  return out;
}

function wordsCovered(pageEl, rect) {
  const hits = pageWords(pageEl).filter((run) => {
    const cx = run.box.x + run.box.w / 2;
    const cy = run.box.y + run.box.h / 2;
    return cx >= rect.x && cx <= rect.x + rect.width && cy >= rect.y && cy <= rect.y + rect.height;
  });
  const sorted = hits.sort((a, b) => a.box.y - b.box.y || a.box.x - b.box.x);
  const lines = [];
  for (const run of sorted) {
    const cy = run.box.y + run.box.h / 2;
    let line = lines.find((ln) => Math.abs(ln.cy - cy) < Math.max(run.box.h, 0.01) * 0.6);
    if (!line) {
      line = { cy, items: [] };
      lines.push(line);
    }
    line.items.push(run);
  }
  return lines
    .map((line) => line.items.sort((a, b) => a.box.x - b.box.x).map((run) => run.text).join(" "))
    .join("\n");
}

// A text selection in the viewer: the words, the page, and one box per line.
function selectionFrom(sel) {
  const text = sel.toString().replace(/[^\S\n]+/g, " ").replace(/\n{2,}/g, "\n").trim();
  if (!text) return null;
  const range = sel.getRangeAt(0);
  const start = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
  const pageEl = start?.closest?.(".page");
  if (!pageEl) return null;
  const box = pageEl.getBoundingClientRect();
  if (!box.width || !box.height) return null;
  const lines = [...range.getClientRects()]
    .filter((r) => r.width > 1 && r.height > 1)
    .map((r) => ({
      x: rounded((r.left - box.left) / box.width),
      y: rounded((r.top - box.top) / box.height),
      width: rounded(r.width / box.width),
      height: rounded(r.height / box.height),
    }));
  if (!lines.length) return null;
  const x = Math.min(...lines.map((l) => l.x));
  const y = Math.min(...lines.map((l) => l.y));
  const right = Math.max(...lines.map((l) => l.x + l.width));
  const bottom = Math.max(...lines.map((l) => l.y + l.height));
  return {
    text,
    page: Number(pageEl.dataset.pageNumber) || 1,
    lines,
    region: { x: rounded(x), y: rounded(y), width: rounded(right - x), height: rounded(bottom - y) },
  };
}

// Add the drawing layer and its controls to a preview stage. A PDF opens in
// Mozilla's viewer, which is on our origin, so the page and the selected
// words are readable. Highlight is a text selection. The page box remains
// only when that viewer does not come up.
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
  // The clip is the viewer frame. The layer sits on the current page inside
  // it, so a drag is the page and the toolbar stays clickable.
  const clip = kind === "pdf" ? el("div", "vclip") : null;
  if (clip) {
    clip.append(layer);
    stage.classList.add("pdfview");
    stage.append(clip, tools);
  } else {
    stage.append(layer, tools);
  }
  tray.layer(layer);

  const viewerPage = () => {
    const app = target.contentWindow?.PDFViewerApplication;
    if (app?.pdfDocument) return app.pdfViewer?.currentPageNumber || 1;
    return page ? Math.max(1, Number(page.value) || 1) : 1;
  };
  const pageNode = () => target.contentDocument?.querySelector(`.page[data-page-number="${viewerPage()}"]`) ?? null;
  const placeLayer = () => {
    const outer = stage.getBoundingClientRect();
    if (!clip) {
      const inner = target.getBoundingClientRect();
      layer.style.left = `${inner.left - outer.left + stage.scrollLeft}px`;
      layer.style.top = `${inner.top - outer.top + stage.scrollTop}px`;
      layer.style.width = `${inner.width}px`;
      layer.style.height = `${inner.height}px`;
      return;
    }
    const frame = target.getBoundingClientRect();
    clip.style.left = `${frame.left - outer.left + stage.scrollLeft}px`;
    clip.style.top = `${frame.top - outer.top + stage.scrollTop}px`;
    clip.style.width = `${frame.width}px`;
    clip.style.height = `${frame.height}px`;
    const pageBox = pageNode()?.getBoundingClientRect();
    if (pageBox?.width) {
      layer.style.left = `${pageBox.left}px`;
      layer.style.top = `${pageBox.top}px`;
      layer.style.width = `${pageBox.width}px`;
      layer.style.height = `${pageBox.height}px`;
    } else {
      layer.style.left = "0";
      layer.style.top = "0";
      layer.style.width = "100%";
      layer.style.height = "100%";
    }
  };
  const sync = () => {
    placeLayer();
    tray.pins();
  };
  let onScroll = null;
  const onPdfSelect = () => {
    if (tool !== "highlight") return;
    const sel = target.contentDocument?.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const taken = selectionFrom(sel);
    if (!taken) return;
    sel.removeAllRanges();
    const at = { type: "highlight", ...taken.region, lines: taken.lines };
    const { annotation, where, text } = payload("highlight", { region: taken.region, text: taken.text, page: taken.page });
    tray.pdfPage = taken.page;
    tray.add({ locator: `${where} highlight`, quote: text, at, annotation });
  };
  let armed = false;
  const armViewer = () => {
    const app = target.contentWindow?.PDFViewerApplication;
    if (!app?.initializedPromise) return;
    const ready = () => {
      if (armed || !app.pdfDocument) return;
      armed = true;
      page?.closest(".vapage")?.remove();
      page = null;
      tray.pdfPage = viewerPage();
      app.eventBus?.on("pagechanging", () => {
        tray.pdfPage = viewerPage();
        sync();
      });
      app.eventBus?.on("scalechanging", placeLayer);
      const scroller = target.contentDocument?.getElementById("viewerContainer");
      onScroll = placeLayer;
      scroller?.addEventListener("scroll", onScroll, { passive: true });
      target.contentDocument?.addEventListener("pointerup", onPdfSelect);
      sync();
    };
    app.initializedPromise.then(() => {
      if (app.pdfDocument) ready();
      else app.eventBus?.on("pagesloaded", ready);
    }).catch(() => sync());
  };
  target.addEventListener("load", () => {
    sync();
    armViewer();
  });
  const resize = new ResizeObserver(sync);
  resize.observe(stage);
  resize.observe(target);
  requestAnimationFrame(sync);

  let tool = "off";
  // How long the PDF keeps the wheel after a notch hits the layer. The layer
  // comes back on its own, or at once when a tool is chosen again.
  const PASS_MS = 2500;
  let passTimer = 0;
  const endPass = () => {
    clearTimeout(passTimer);
    passTimer = 0;
    layer.classList.remove("passing");
  };
  const select = (name) => {
    tool = name;
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.tool === name));
    layer.classList.toggle("active", name !== "off");
    stage.classList.toggle("text-select", kind === "pdf" && name === "highlight");
    endPass();
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
    if (kind === "pdf") annotation.page = extra.page || viewerPage();
    const { page: _page, text, ...rest } = extra;
    if (text) annotation.text = text;
    Object.assign(annotation, rest);
    const where = kind === "pdf" ? `page ${annotation.page}` : "image";
    return { annotation, where, text: text || "" };
  };
  const clear = () => {
    start = null;
    pts = null;
    draft?.remove();
    draft = null;
  };
  // The viewer's scroll container is inside the frame, so a wheel whose
  // target is the layer never moves the PDF. Stepping the layer out of the
  // hit test lets the rest of the gesture through (#919). An image scrolls
  // an ancestor of the layer, which a wheel already reaches.
  const onWheel = () => {
    if (kind !== "pdf" || tool === "off" || start) return;
    layer.classList.add("passing");
    clearTimeout(passTimer);
    passTimer = setTimeout(endPass, PASS_MS);
  };
  layer.addEventListener("wheel", onWheel, { passive: true });

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
      const node = kind === "pdf" ? pageNode() : null;
      const hit = node && pageWords(node).find((run) =>
        end.x >= run.box.x && end.x <= run.box.x + run.box.w && end.y >= run.box.y && end.y <= run.box.y + run.box.h);
      const { annotation, where, text } = payload("point", {
        region: { x: at.x, y: at.y, width: 0, height: 0 },
        text: hit?.text || "",
      });
      tray.add({ locator: `${where} point`, quote: text, at, annotation });
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
    const node = kind === "pdf" ? pageNode() : null;
    const covered = node ? wordsCovered(node, box) : "";
    const at = { type, ...box };
    const { annotation, where, text } = payload(type, { region: box, text: covered });
    tray.add({ locator: `${where} ${type}`, quote: text, at, annotation });
  });
  layer.addEventListener("pointercancel", clear);
  if (target.contentDocument?.readyState === "complete") armViewer();
  return {
    sync,
    destroy: () => {
      resize.disconnect();
      clearTimeout(passTimer);
      layer.removeEventListener("wheel", onWheel);
      if (onScroll) target.contentDocument?.getElementById("viewerContainer")?.removeEventListener("scroll", onScroll);
      target.contentDocument?.removeEventListener("pointerup", onPdfSelect);
    },
  };
}
