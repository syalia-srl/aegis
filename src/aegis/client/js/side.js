// The session panel's behaviour: the card each compact row opens, the panel's
// width, and collapsing it. What the rows say is the sidebar part's
// (parts/sidebar.js); this file only moves them.
//
// A row (.prow) holds its own card (.pcard) in the markup, so the code that
// fills the panel fills the card too, and a card stays current while it is
// open. On a desktop the card is fixed to the left of the panel, level with its
// row, and opens on hover or focus. In the phone's drawer there is no hover:
// a tap opens the card under its row.
//
// One panel is one Side: the root it sets --side-w and data-side on, the aside
// it measures and the grip that resizes it, all handed in. Two mounts on one
// page therefore keep their own cards, width and drawer.

export const MIN_W = 200;
export const MAX_W = 480;
export const DEFAULT_W = 260;
const NARROW_W = 230; // under this the action buttons drop their labels
const W_KEY = "aegis.side.w";
const CLOSED_KEY = "aegis.side.closed";

const drawer = matchMedia("(max-width: 760px)");
const hover = matchMedia("(hover: hover)");

export class Side {
  constructor({ root, side, grip, onCardOpen = () => {} }) {
    this.root = root;
    this.side = side;
    this.grip = grip;
    this.onOpen = onCardOpen;
    this.openEl = null;
    this.openT = null;
    this.closeT = null;

    for (const el of side.querySelectorAll(".prow")) this.wire(el);
    this.setWidth(Number(localStorage.getItem(W_KEY)) || DEFAULT_W, false);
    this.restState();

    this.resize = new ResizeObserver(() => {
      side.classList.toggle("narrow", side.clientWidth > 0 && side.clientWidth < NARROW_W);
      this.place();
    });
    this.resize.observe(side);
    this.onScroll = () => this.place();
    side.addEventListener("scroll", this.onScroll);
    this.onWindowResize = () => this.place();
    addEventListener("resize", this.onWindowResize);
    // A card that grows (a quota reading lands) stays inside the window.
    this.cards = new ResizeObserver(() => this.place());
    for (const c of side.querySelectorAll(".pcard")) this.cards.observe(c);

    this.onDown = (ev) => this.down(ev);
    grip.addEventListener("pointerdown", this.onDown);
    this.onDouble = () => this.setWidth(DEFAULT_W);
    grip.addEventListener("dblclick", this.onDouble);
  }

  collapsed() {
    return localStorage.getItem(CLOSED_KEY) === "1";
  }

  setWidth(w, save = true) {
    w = Math.max(MIN_W, Math.min(MAX_W, Math.round(w)));
    this.root.style.setProperty("--side-w", `${w}px`);
    if (save) localStorage.setItem(W_KEY, String(w));
    return w;
  }

  // The desktop panel: shown, or collapsed out of the grid. The phone's drawer
  // is data-side=open, and the page closes it back to this state.
  restState() {
    if (this.collapsed()) this.root.dataset.side = "closed";
    else delete this.root.dataset.side;
  }

  toggleCollapsed() {
    localStorage.setItem(CLOSED_KEY, this.collapsed() ? "0" : "1");
    this.closeCard();
    this.restState();
  }

  place() {
    if (!this.openEl || drawer.matches) return;
    const card = this.openEl.querySelector(":scope > .pcard");
    const r = this.openEl.getBoundingClientRect();
    const s = this.side.getBoundingClientRect();
    if (!r.height) return this.closeCard();
    card.style.left = `${Math.max(8, s.left - card.offsetWidth - 12)}px`;
    card.style.top = `${Math.max(12, Math.min(r.top - 8, innerHeight - card.offsetHeight - 12))}px`;
  }

  open(el) {
    clearTimeout(this.closeT);
    clearTimeout(this.openT);
    if (this.openEl === el) return;
    if (this.openEl) this.openEl.classList.remove("open");
    this.openEl = el;
    el.classList.add("open");
    this.onOpen();
    this.place();
  }

  // True when a card was open, for the Esc chain.
  closeCard() {
    clearTimeout(this.openT);
    clearTimeout(this.closeT);
    if (!this.openEl) return false;
    this.openEl.classList.remove("open");
    this.openEl = null;
    return true;
  }

  soonClose() {
    clearTimeout(this.openT);
    clearTimeout(this.closeT);
    this.closeT = setTimeout(() => this.closeCard(), 160);
  }

  wire(el) {
    el.addEventListener("mouseenter", () => {
      if (!hover.matches || drawer.matches) return;
      clearTimeout(this.closeT);
      clearTimeout(this.openT);
      this.openT = setTimeout(() => this.open(el), this.openEl ? 0 : 140);
    });
    el.addEventListener("mouseleave", () => {
      if (hover.matches && !drawer.matches) this.soonClose();
    });
    // Keyboard: the row takes focus and its card follows it.
    // Only keyboard focus: a click or a tap focuses the row too, and the tap's
    // own click would close what its focus just opened.
    el.addEventListener("focusin", (ev) => {
      if (ev.target === el && el.matches(":focus-visible")) this.open(el);
    });
    el.addEventListener("focusout", (ev) => {
      if (!el.contains(ev.relatedTarget)) this.soonClose();
    });
    // Touch, or the drawer: a tap on the row toggles its card. A tap inside the
    // card, or on a name being renamed, is the card's own.
    el.addEventListener("click", (ev) => {
      if (hover.matches && !drawer.matches) return;
      if (ev.target.closest(".pcard, .editable, input, a")) return;
      if (this.openEl === el) this.closeCard();
      else this.open(el);
    });
  }

  down(ev) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    this.grip.setPointerCapture(ev.pointerId);
    this.root.dataset.resizing = "";
    this.closeCard();
    const right = this.side.getBoundingClientRect().right;
    const move = (e) => this.setWidth(right - e.clientX);
    const up = () => {
      delete this.root.dataset.resizing;
      this.grip.removeEventListener("pointermove", move);
      this.grip.removeEventListener("pointerup", up);
      this.grip.removeEventListener("pointercancel", up);
    };
    this.grip.addEventListener("pointermove", move);
    this.grip.addEventListener("pointerup", up);
    this.grip.addEventListener("pointercancel", up);
  }

  destroy() {
    this.resize.disconnect();
    this.cards.disconnect();
    this.side.removeEventListener("scroll", this.onScroll);
    removeEventListener("resize", this.onWindowResize);
    this.grip.removeEventListener("pointerdown", this.onDown);
    this.grip.removeEventListener("dblclick", this.onDouble);
    this.closeCard();
  }
}
