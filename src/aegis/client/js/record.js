// Hold the mic to record (#932): a tap on a composer's mic still dictates; a
// press held HOLD_MS records audio with the browser's own microphone until it is
// released, which sends the take. Sliding up LOCK_PX while holding locks the
// recording, so minutes of narration need no held finger, and the bar's stop
// button (or the mic) then ends and sends it; its bin throws it away, as Esc
// does in both modes. The mouse does the same on a desktop.
//
// The browser records Opus at a low bitrate where it can (WebM in Chromium and
// Firefox, MP4 in Safari) and the file goes up as an attachment marked with the
// take's length; the server rewrites it as mono 16 kHz Opus when the message is
// sent (recordings.py), because browsers do not reliably honor a requested
// sample rate. This is the one place audio leaves the page, and only because
// the person chose to send it (DESIGN.md, "Audio never leaves the browser").

import { icon } from "./glyphs.js";

export const HOLD_MS = 400;
export const LOCK_PX = 60;
// A take shorter than this is a slip of the finger, not a message.
const MIN_MS = 700;
const BITRATE = 32000;
const TYPES = [
  ["audio/webm;codecs=opus", "webm"],
  ["audio/ogg;codecs=opus", "ogg"],
  ["audio/mp4;codecs=opus", "m4a"],
  ["audio/mp4", "m4a"],
];

function clock(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export class HoldToRecord {
  // `busy()`: whether a long press must stay a tap (dictation running);
  // `onDone(file, ms, ctx)`: a finished take, with what `begin` was given;
  // `onError(message)`.
  constructor({ busy, onDone, onError }) {
    this.busy = busy;
    this.onDone = onDone;
    this.onError = onError;
    this.rec = null; // the take in progress, or the one starting
  }

  // Esc (keys.js): throw the take away; whether there was one.
  discard() {
    if (!this.rec) return false;
    this.stop(false);
    return true;
  }

  get active() {
    return !!this.rec;
  }

  // Wire `button`: a long press records into the box around it, for the
  // context `ctx()` names at the press (the session the take belongs to).
  attach(button, ctx) {
    const box = button.closest(".box");
    let press = null;
    let swallow = false;
    button.addEventListener("contextmenu", (e) => e.preventDefault());
    // A press that recorded is not also a tap: the click it ends in is dropped
    // before the dictation's own listener sees it.
    button.addEventListener("click", (e) => {
      if (!swallow) return;
      swallow = false;
      e.preventDefault();
      e.stopImmediatePropagation();
    }, true);
    button.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || button.disabled) return;
      if (this.rec?.locked) {
        // A tap on the mic of a locked take ends it, as the stop button does.
        swallow = true;
        this.stop(true);
        return;
      }
      if (this.rec || this.busy()) return;
      swallow = false;
      button.setPointerCapture(e.pointerId);
      press = {
        y: e.clientY,
        timer: setTimeout(() => {
          swallow = true;
          this.begin(box, button, ctx());
        }, HOLD_MS),
      };
    });
    button.addEventListener("pointermove", (e) => {
      const r = this.rec;
      if (!press || !r || r.locked || r.box !== box) return;
      const up = Math.max(0, press.y - e.clientY);
      r.bar.style.setProperty("--lift", Math.min(1, up / LOCK_PX).toFixed(2));
      if (up >= LOCK_PX) this.lock();
    });
    const release = () => {
      if (!press) return;
      clearTimeout(press.timer);
      press = null;
      if (this.rec && this.rec.box === box && !this.rec.locked) this.stop(true);
    };
    button.addEventListener("pointerup", release);
    button.addEventListener("pointercancel", release);
  }

  async begin(box, button, ctx) {
    const bar = this.bar(box);
    const r = (this.rec = { box, button, ctx, bar, locked: false, stopped: false, send: false });
    bar.dataset.state = "starting";
    bar.hidden = false;
    button.dataset.rec = "on";
    let stream;
    try {
      // Raw voice: echo cancelling and noise suppression smear a narration.
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false },
      });
    } catch (e) {
      this.end(r);
      this.onError(`Cannot record: ${e.message || e.name}`);
      return;
    }
    if (r.stopped) {
      // Released while the browser was still asking for the microphone.
      for (const t of stream.getTracks()) t.stop();
      this.end(r);
      return;
    }
    const [mime, ext] = TYPES.find(([t]) => MediaRecorder.isTypeSupported(t)) || ["", "webm"];
    const mr = new MediaRecorder(stream, { ...(mime && { mimeType: mime }), audioBitsPerSecond: BITRATE });
    const chunks = [];
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mr.onstop = () => {
      for (const t of stream.getTracks()) t.stop();
      const ms = performance.now() - r.t0;
      this.end(r);
      if (!r.send) return;
      if (ms < MIN_MS) {
        this.onError("Too short to send: hold the microphone while you speak");
        return;
      }
      const type = (mr.mimeType || mime || "audio/webm").split(";")[0];
      const file = new File(chunks, `recording.${ext}`, { type });
      file.recordedMs = Math.round(ms);
      this.onDone(file, file.recordedMs, r.ctx);
    };
    r.mr = mr;
    r.t0 = performance.now();
    mr.start(1000);
    bar.dataset.state = "held";
    const tick = () => (bar.querySelector(".rec-time").textContent = clock(performance.now() - r.t0));
    tick();
    r.tick = setInterval(tick, 250);
  }

  lock() {
    if (!this.rec || this.rec.locked) return;
    this.rec.locked = true;
    this.rec.bar.dataset.state = "locked";
  }

  // End the take: `send` it, or throw it away.
  stop(send) {
    const r = this.rec;
    if (!r || r.stopped) return;
    r.stopped = true;
    r.send = send;
    if (r.mr && r.mr.state !== "inactive") r.mr.stop();
    else if (r.mr) this.end(r);
    // Without a recorder yet, begin() ends it once the microphone answers.
  }

  end(r) {
    clearInterval(r.tick);
    r.bar.hidden = true;
    r.bar.style.removeProperty("--lift");
    delete r.button.dataset.rec;
    if (this.rec === r) this.rec = null;
  }

  // The box's recording bar, made on first use.
  bar(box) {
    let bar = box.querySelector(".rec-bar");
    if (bar) return bar;
    bar = document.createElement("div");
    bar.className = "rec-bar";
    bar.hidden = true;
    bar.innerHTML =
      '<span class="rec-dot" aria-hidden="true"></span><span class="rec-time">0:00</span>' +
      '<span class="rec-hint held">Release to send · slide up to lock</span>' +
      '<span class="rec-hint locked">Recording · stop sends it</span>' +
      '<button type="button" class="rec-cancel" title="Discard (Esc)" aria-label="Discard the recording"></button>' +
      '<button type="button" class="rec-stop" title="Stop and send" aria-label="Stop and send the recording"></button>';
    bar.querySelector(".rec-cancel").append(icon("trash"));
    bar.querySelector(".rec-stop").append(icon("stop"));
    bar.querySelector(".rec-hint.held").prepend(icon("lock"), " ");
    bar.querySelector(".rec-cancel").addEventListener("click", () => this.stop(false));
    bar.querySelector(".rec-stop").addEventListener("click", () => this.stop(true));
    box.append(bar);
    return bar;
  }
}
