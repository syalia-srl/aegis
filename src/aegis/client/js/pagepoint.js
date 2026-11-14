// Pointing inside a sandboxed page (#479, spec 07). The server adds this
// script to every HTML preview, artifacts included (web.py, inject_point);
// the page runs in an opaque origin, so the client cannot read a selection or
// an element inside it, and this script reports them to the host instead.
//
// It posts {jsonrpc, method: "aegis/point", params: {kind, x, y, rect,
// locator: {heading, id, css}, text}} to the parent on a gesture by the
// person: "menu" on a right-click (not with Shift) or a touch held 500 ms
// without moving 8 px, "select" when a selection settles, "clear" when it
// goes or the page scrolls, and "pick" when annotate mode is on and the
// person clicks an element (ArrowUp/Down walks the parent or child). A field,
// a link, an image or a press with no text under it keeps the browser's own
// menu. The host draws the menu or the bar in its own document, and acts only
// on an item the person picks there: a message proposes, nothing more. While
// the host says its menu is open (aegis/point-open, aegis/point-closed), the
// element is outlined. Annotate mode is toggled with aegis/annotate.
//
// A plain script with no globals: a page's own code never sees it.
(() => {
  if (window.parent === window) return;
  const LONG_PRESS = 500;
  const SLOP = 8;
  const TEXT_MAX = 300;
  const NATIVE = "input, textarea, select, [contenteditable], a[href], img, video, audio, canvas, svg";
  const STYLE_KEYS = ["color", "backgroundColor", "fontSize", "fontFamily", "display", "position"];
  const post = (params) => window.parent.postMessage({ jsonrpc: "2.0", method: "aegis/point", params }, "*");

  const elOf = (n) => (n && n.nodeType === 1 ? n : n?.parentElement) || document.body;

  // The text of the nearest heading at or before `el` in document order.
  function headingOf(el) {
    let found = null;
    for (const h of document.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
      if (h === el || h.contains(el) || h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) found = h;
      else break;
    }
    return found ? found.textContent.trim().replace(/\s+/g, " ") : null;
  }

  // The element's id, or a CSS path of at most four steps, stopping at an
  // ancestor with an id.
  function cssOf(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const steps = [];
    for (let n = el; n && n !== document.body && n !== document.documentElement && steps.length < 4; n = n.parentElement) {
      if (n.id) {
        steps.unshift(`#${CSS.escape(n.id)}`);
        break;
      }
      const tag = n.localName;
      const same = n.parentElement ? [...n.parentElement.children].filter((c) => c.localName === tag) : [];
      steps.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(n) + 1})` : tag);
    }
    return steps.join(" > ") || "body";
  }

  const cut = (t) => {
    const s = t.trim().replace(/[ \t]+\n/g, "\n");
    return s.length > TEXT_MAX ? `${s.slice(0, TEXT_MAX)}…` : s;
  };

  function locate(el, text) {
    return { locator: { heading: headingOf(el), id: el.id || null, css: cssOf(el) }, text: cut(text) };
  }

  function stylesOf(el) {
    const cs = getComputedStyle(el);
    return Object.fromEntries(STYLE_KEYS.map((k) => [k, cs[k]]));
  }

  function rectOf(el) {
    const b = el.getBoundingClientRect();
    return { left: b.left, top: b.top, right: b.right, bottom: b.bottom };
  }

  const selected = () => {
    const sel = getSelection();
    return sel.rangeCount && !sel.isCollapsed && sel.toString().trim() ? sel.getRangeAt(0) : null;
  };

  let pointed = null; // the element the last menu proposed or open is about
  const open = new Map(); // the host's menu number -> {el, outline}: its own, to put back
  let annotate = false; // host toggles with aegis/annotate: pick elements for the tray
  let hover = null; // {el, outline} while annotate mode is on
  let pick = null; // the element ArrowUp/Down walks from

  function clearHover() {
    if (!hover) return;
    if (![...open.values()].some((o) => o.el === hover.el) && hover.el !== pointed) {
      hover.el.style.outline = hover.outline;
    }
    hover = null;
  }

  function setHover(el) {
    if (!el || el === document.body || el === document.documentElement) return clearHover();
    if (hover?.el === el) return;
    clearHover();
    hover = { el, outline: el.style.outline };
    el.style.outline = "2px solid #2f6fdb";
  }

  function pickable(target) {
    const el = elOf(target);
    if (el === document.body || el === document.documentElement) return null;
    if (el.closest(NATIVE) && !el.matches("img, svg")) return null;
    return el;
  }

  function emitPick(el) {
    pick = el;
    setHover(el);
    const text = (el.innerText || el.textContent || "").trim();
    post({
      kind: "pick",
      tag: el.localName,
      styles: stylesOf(el),
      rect: rectOf(el),
      ...locate(el, text),
    });
  }

  function stepPick(dir) {
    if (!pick) return;
    if (dir < 0) {
      const parent = pick.parentElement;
      if (parent && parent !== document.documentElement) emitPick(parent);
      return;
    }
    const child = [...pick.children].find((c) => c.nodeType === 1);
    if (child) emitPick(child);
  }

  // What a press at x, y on `target` is about: the selection under it, else
  // the element and its text. Null leaves the browser its own menu: a field
  // (paste), a link, an image or a canvas, the page's empty space, no text.
  function aboutAt(target, x, y) {
    const r = selected();
    if (r && [...r.getClientRects()].some((b) => x >= b.left && x <= b.right && y >= b.top && y <= b.bottom)) {
      return { el: elOf(r.commonAncestorContainer), text: r.toString() };
    }
    const el = elOf(target);
    if (el === document.body || el === document.documentElement || el.closest(NATIVE)) return null;
    const text = (el.innerText || el.textContent || "").trim();
    return text ? { el, text } : null;
  }

  function menuAt({ el, text }, x, y, touch) {
    pointed = el;
    post({ kind: "menu", x, y, touch, ...locate(el, text) });
  }

  // On the window, so the page's own listeners run first: a page with a
  // right-click or a long press of its own (a board, an editor, its own
  // menu) cancels the event, and gets no host menu over its own.
  window.addEventListener("contextmenu", (ev) => {
    if (annotate || ev.shiftKey) return;
    clearTimeout(timer); // a touch's contextmenu before the long press's timer: one menu, not two
    if (swallow) return ev.preventDefault(); // the long press already reported it
    if (ev.defaultPrevented) return;
    const about = aboutAt(ev.target, ev.clientX, ev.clientY);
    if (!about) return;
    ev.preventDefault();
    menuAt(about, ev.clientX, ev.clientY, false);
  });

  let timer = null;
  let at = null;
  let swallow = false;
  window.addEventListener("pointerdown", (ev) => {
    swallow = false;
    // A press anywhere in the page is a press outside the host's menu: the
    // host never sees it, so it is told.
    if (pointed) post({ kind: "clear" });
    clearTimeout(timer);
    if (annotate) return;
    if (ev.pointerType === "mouse" || ev.defaultPrevented || ev.target.closest?.(NATIVE)) return;
    at = [ev.clientX, ev.clientY];
    const target = ev.target;
    timer = setTimeout(() => {
      const about = aboutAt(target, at[0], at[1]);
      if (!about) return;
      swallow = true;
      window.focus(); // the press's frame, as a click's would be: the host checks it
      menuAt(about, at[0], at[1], true);
    }, LONG_PRESS);
  });
  document.addEventListener("pointermove", (ev) => {
    if (annotate) {
      const el = pickable(document.elementFromPoint(ev.clientX, ev.clientY) || ev.target);
      if (el) setHover(el);
      return;
    }
    if (at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > SLOP) clearTimeout(timer);
  });
  const settle = () => {
    clearTimeout(timer);
    if (!annotate) setTimeout(report); // a click inside a selection collapses it after pointerup
  };
  document.addEventListener("pointerup", settle);
  document.addEventListener("pointercancel", () => clearTimeout(timer));
  // The release's mousedown would move the focus into the frame, which
  // closes the host's menu through its focusout.
  document.addEventListener("mousedown", (ev) => swallow && ev.preventDefault(), true);
  document.addEventListener(
    "click",
    (ev) => {
      if (annotate) {
        const el = pickable(ev.target);
        if (!el) return;
        ev.preventDefault();
        ev.stopPropagation();
        window.focus();
        emitPick(el);
        return;
      }
      if (!swallow) return;
      swallow = false;
      ev.preventDefault();
      ev.stopPropagation();
    },
    true,
  );

  // A selection that settled, or one that went.
  let shown = "";
  function report() {
    if (annotate) return;
    const r = selected();
    const text = r ? r.toString() : "";
    if (text === shown) return;
    shown = text;
    if (!r) return post({ kind: "clear" });
    const b = r.getClientRects()[0] || r.getBoundingClientRect();
    post({ kind: "select", rect: { left: b.left, top: b.top, right: b.right, bottom: b.bottom }, ...locate(elOf(r.commonAncestorContainer), text) });
  }
  // A handle dragged on touch changes the selection with no pointerup here.
  let quiet = null;
  document.addEventListener("selectionchange", () => {
    clearTimeout(quiet);
    quiet = setTimeout(report, 300);
  });

  // Esc closes the host's menu: the focus stays in this frame while it is open.
  // ArrowUp/Down walks the annotate pick to a parent or child.
  document.addEventListener("keydown", (ev) => {
    if (annotate && (ev.key === "ArrowUp" || ev.key === "ArrowDown")) {
      ev.preventDefault();
      stepPick(ev.key === "ArrowUp" ? -1 : 1);
      return;
    }
    if (ev.key === "Escape" && (pointed || shown)) {
      shown = "";
      post({ kind: "clear" });
    }
  });

  // The host draws at fixed points: a scroll here moves the element away
  // from its menu or its bar, so they close.
  document.addEventListener(
    "scroll",
    () => {
      clearTimeout(quiet); // a report still due would bring the bar back where it was
      if (annotate) return;
      if (!pointed && !shown) return;
      shown = "";
      post({ kind: "clear" });
    },
    true,
  );

  // A press in another frame, or anywhere the host cannot see, takes the
  // focus from this page: what the host drew for it goes.
  window.addEventListener("blur", () => {
    if (annotate) return;
    if (!pointed && !shown) return;
    shown = "";
    post({ kind: "clear" });
  });

  window.addEventListener("message", (ev) => {
    if (ev.source !== window.parent || ev.data?.jsonrpc !== "2.0") return;
    if (ev.data.method === "aegis/annotate") {
      annotate = !!ev.data.params?.active;
      if (!annotate) {
        clearHover();
        pick = null;
      }
      return;
    }
    // Each message names its menu: the host closes the old one after it
    // opened the new, so a closed message ends only its own.
    const n = ev.data.params?.n;
    if (ev.data.method === "aegis/point-open" && pointed) {
      const earlier = [...open.values()].find((o) => o.el === pointed);
      open.set(n, { el: pointed, outline: earlier ? earlier.outline : pointed.style.outline });
      pointed.style.outline = "2px solid #2f6fdb";
    } else if (ev.data.method === "aegis/point-closed" && open.has(n)) {
      const { el, outline } = open.get(n);
      open.delete(n);
      if (![...open.values()].some((o) => o.el === el) && hover?.el !== el) el.style.outline = outline;
      if (el === pointed && !open.size) pointed = null;
    }
  });
})();
