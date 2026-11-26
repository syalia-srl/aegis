import { micSource } from "./dictation.js";
import { attachmentTake, HOLD_MS } from "./record.js";
import { icon } from "./glyphs.js";

function control(className, text) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  return button;
}

// A microphone opened before the outcome is known. Replay the leading PCM
// into the chosen reader, then hand future samples directly to it.
class PressCapture {
  constructor() {
    this.buffer = [];
    this.mode = null;
    this.ready = micSource(
      (audio) => {
        if (this.samples) this.samples(audio);
        else if (!this.mode) this.buffer.push(audio);
      },
      (reason) => {
        this.ended = reason;
        this.onEnd?.(reason);
      },
      (stream) => {
        if (this.mode && this.mode !== "audio" || !globalThis.MediaRecorder) return;
        // PCM capture still works in a browser where native encoding fails.
        try { this.take = attachmentTake(stream); }
        catch (error) { this.audioError = error; }
      },
    );
    this.ready.catch(() => {});
  }

  choose(mode) {
    this.mode = mode;
    if (mode !== "audio") {
      this.take?.();
      this.take = null;
    }
  }

  source(samples, onEnd) {
    for (const audio of this.buffer.splice(0)) samples(audio);
    this.samples = samples;
    this.onEnd = onEnd;
    if (this.ended) this.ready.then(() => onEnd(this.ended));
    return this.ready;
  }

  async close() {
    this.buffer = [];
    const stop = await this.ready;
    await stop();
  }

  async send() {
    try {
      await this.ready;
      if (this.audioError) throw this.audioError;
      return await this.take?.();
    } finally {
      await this.close();
    }
  }
}

export class MicChooser {
  constructor(button, { t, available, tap, context, send, live, error }) {
    const wrap = document.createElement("span");
    wrap.className = "mic-chooser";
    button.replaceWith(wrap);
    const options = control("mic mic-options", "");
    options.setAttribute("aria-label", t("dictation.modes"));
    options.setAttribute("aria-expanded", "false");
    options.append(icon("caret-d"));
    const panel = document.createElement("span");
    panel.className = "mic-targets";
    panel.hidden = true;
    const realtime = control("btn", t("dictation.realtime"));
    realtime.dataset.mode = "realtime";
    const audio = control("btn", t("dictation.sendAudio"));
    audio.dataset.mode = "audio";
    panel.append(realtime, audio);
    wrap.append(button, options, panel);
    let press = null;
    let swallow = false;
    const show = () => {
      panel.hidden = false;
      options.setAttribute("aria-expanded", "true");
    };
    const clear = () => {
      if (!press) return null;
      const current = press;
      clearTimeout(current.timer);
      press = null;
      panel.hidden = true;
      options.setAttribute("aria-expanded", "false");
      delete button.dataset.rec;
      for (const target of panel.children) target.classList.remove("on");
      return current;
    };
    const finish = (mode) => {
      const current = clear();
      if (!current) return false;
      const { capture, ctx } = current;
      capture.choose(mode);
      if (mode === "audio") capture.send().then((file) => send(file, ctx)).catch(error);
      else if (mode === "realtime") live(ctx, capture.source.bind(capture));
      else if (mode === "dictation") tap(ctx, capture.source.bind(capture));
      else capture.close().catch(() => {});
      return true;
    };
    const activeTarget = (event) => {
      let picked;
      for (const target of panel.children) {
        const r = target.getBoundingClientRect();
        const on = !panel.hidden && event.clientX >= r.left && event.clientX <= r.right &&
          event.clientY >= r.top && event.clientY < r.bottom;
        target.classList.toggle("on", on);
        if (on) picked = target.dataset.mode;
      }
      return picked;
    };
    const begin = (id) => {
      const ctx = context();
      press = { id, ctx, capture: new PressCapture() };
      button.dataset.rec = "on";
    };
    const keyboard = () => {
      if (press) return;
      if (!available()) return;
      begin(null);
      show();
      audio.focus();
    };

    button.addEventListener("contextmenu", (event) => event.preventDefault());
    button.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || press || button.disabled) return;
      swallow = false;
      if (!available()) return;
      event.preventDefault();
      button.focus({ preventScroll: true });
      button.setPointerCapture(event.pointerId);
      begin(event.pointerId);
      swallow = true;
      press.timer = setTimeout(() => {
        show();
        activeTarget(press.position);
      }, HOLD_MS);
      press.position = event;
    });
    button.addEventListener("pointermove", (event) => {
      if (press?.id !== event.pointerId) return;
      press.position = event;
      activeTarget(event);
    });
    button.addEventListener("pointerup", (event) => {
      if (press?.id === event.pointerId) finish(activeTarget(event) || "dictation");
    });
    for (const event of ["pointercancel", "lostpointercapture"])
      button.addEventListener(event, (e) => press?.id === e.pointerId && finish("cancel"));
    button.addEventListener("click", (event) => {
      if (event.detail && swallow) {
        swallow = false;
        return;
      }
      if (!press) tap(context());
    });
    options.addEventListener("click", keyboard);
    for (const target of panel.children) target.addEventListener("click", () => {
      button.focus();
      finish(target.dataset.mode);
    });
    wrap.addEventListener("focusout", (event) => {
      if (press?.id === null && !wrap.contains(event.relatedTarget)) finish("cancel");
    });
    wrap.addEventListener("keydown", (event) => {
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        if (panel.hidden) keyboard();
        else (event.key === "ArrowUp" ? realtime : audio).focus();
      } else if (event.key === "Escape" && press) {
        event.preventDefault();
        event.stopPropagation();
        button.focus();
        finish("cancel");
      }
    });
    if (button.disabled) options.disabled = true;
    this.discard = () => finish("cancel");
  }
}
