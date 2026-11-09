// The composer's dictation as a part: the mic button built into
// [data-slot=mic], and everything behind it.
//
// One part owns one Dictation engine and one HoldToRecord for the page or the
// mount it lives in. Its first box is the composer it was given; `add()` takes
// another box for the same engine, which is how the full layout gives the new
// tab's first-message box a mic until b5 moves that view into a part of its
// own. A box supplies what the engine needs and nothing page-wide: its
// textarea, the draft key the textarea is showing, what to do when text lands
// and what to do with a finished hold-to-record take.
//
// A target's key is the draft it started in, read when the recording starts;
// `current()` is the draft the box shows now, so text that arrives after the
// box moved to another session goes to the draft it was spoken into
// (dictation.js, `doc`).
//
// The engine opens Whisper's workers in the browser, so a mount that never
// turns dictation on builds no button and enters no code path.

import { Dictation } from "../dictation.js";
import { HoldToRecord } from "../record.js";
import { icon } from "../glyphs.js";
import { q } from "../dom.js";
import { labelsFor } from "../labels.js";
import { toast } from "../toast.js";

// The words the button carries come from the mount's table.
function micButton(t) {
  const b = document.createElement("button");
  b.id = "mic"; // the id the browser suite and client.css address
  b.className = "mic needs-net";
  b.type = "button";
  b.dataset.state = "idle";
  b.title = t("dictation.title");
  b.setAttribute("aria-label", t("dictation.label"));
  b.append(icon("mic"));
  return b;
}

export class DictationPart {
  // host: the composer element that holds [data-slot=mic], or a box whose mic
  // is already in the markup (`mic`). prepare(): the server's dictation.prepare.
  // canStart(): whether the box may start one now (the connection is up).
  // onState/onLevel: the page's own listeners (an artifact frame's dictation).
  constructor(mount, host, opts = {}) {
    this.mount = mount;
    this.t = mount.t || labelsFor("en");
    this.opts = opts;
    this.boxes = new Map(); // textarea -> {input, draft, heard, ctx, onTake}
    this.buttons = new Map(); // button -> the same box
    const refuse = (message) =>
      opts.onError ? opts.onError(message) : toast(message, { kind: "error", root: mount.root });
    this.dictation = new Dictation({
      prepare: opts.prepare || (() => mount.call("dictation.prepare")),
      onState: (state) => {
        for (const [button, box] of this.buttons)
          button.dataset.state = this.dictation.target?.el === box.input ? state : "idle";
        opts.onState?.(state);
      },
      onLevel: (x) => {
        for (const [button] of this.buttons)
          button.style.setProperty("--level", Math.min(1, x * 8).toFixed(2));
        opts.onLevel?.(x);
      },
      onError: refuse,
      t: this.t,
    });
    this.recorder = new HoldToRecord({
      t: this.t,
      busy: () => this.busy(),
      onError: refuse,
      onDone: (file, ms, ctx) => ctx.box.onTake?.(file, ms, ctx.box.ctx?.()),
    });
    this.add(host, opts);
  }

  get state() {
    return this.dictation.state;
  }

  // The engine itself, for the modules that drive a recording from outside a
  // box: an artifact frame's aegis.dictate starts one in a shim field and stops
  // it from the page's own bar (artifacts.js), and only the engine knows how.
  get engine() {
    return this.dictation;
  }

  // A long press stays a tap while a take or a dictation is running, or while
  // the box cannot start one at all (no connection).
  busy() {
    return (this.opts.canStart && !this.opts.canStart()) || this.dictation.state !== "idle";
  }

  allowed() {
    return !this.opts.canStart || this.opts.canStart();
  }

  // Wire one box: its textarea and a mic, built into the host's slot unless the
  // markup already holds it (the new tab's, until b5).
  add(host, opts = {}) {
    const input = typeof opts.input === "function" ? opts.input() : opts.input;
    if (!input) throw new Error("aegis: a dictation box needs its textarea");
    let button = opts.mic || null;
    if (!button) {
      const slot = q(host, '[data-slot="mic"]');
      if (!slot) throw new Error("aegis: the composer has no mic slot");
      button = micButton(this.t);
      slot.append(button);
    }
    const box = {
      input,
      draft: opts.draft || (() => null),
      heard: opts.heard || (() => {}),
      ctx: opts.ctx || (() => null),
      onTake: opts.onTake,
    };
    this.boxes.set(input, box);
    this.buttons.set(button, box);
    if (!navigator.mediaDevices) {
      button.disabled = true;
      button.title = this.t("dictation.needsHttps");
    } else button.title += this.t("dictation.hold");
    this.recorder.attach(button, () => ({ box }));
    button.addEventListener("click", () => {
      if (!this.allowed()) return;
      this.dictation.toggle(this.target(box), "button");
    });
    return button;
  }

  // Where a box's text goes while it records: the draft key it started in, and
  // the draft the box shows now.
  target(box) {
    const key = box.draft();
    return { el: box.input, key, current: () => box.draft(), heard: () => box.heard(key) };
  }

  // Alt+M (the full layout's key table): the box whose textarea it names.
  toggle(input, reason = "key") {
    const box = this.boxes.get(input);
    if (!box || !this.allowed()) return;
    return this.dictation.toggle(this.target(box), reason);
  }

  // A send takes what was said with it; the composer asks before it sends.
  finish(input) {
    return this.dictation.finish(input);
  }

  // A recording belongs to its box: moving that box to another session stops
  // it, as the page's follow() always did.
  stopIfHere(input, reason = "tab") {
    if (this.dictation.target?.el === input) return this.dictation.stop(reason);
  }

  // Esc: throw away the take in progress, if there is one.
  discard() {
    return this.recorder.discard();
  }

  destroy() {
    this.dictation.stop("tab");
    this.recorder.discard();
  }
}
