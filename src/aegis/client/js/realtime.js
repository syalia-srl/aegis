// Count actual captured samples so tab throttling does not move the boundaries.
import { SR, concat, quietest, rms } from "./dictation.js";

const FRAME = SR / 50;
const SILENCE = 10 * SR;
const LIMIT = 60 * SR;

export class VoiceBlocks {
  constructor(emit) {
    this.emit = emit;
    this.pending = new Float32Array(0);
    this.parts = [];
    this.length = 0;
    this.quiet = 0;
  }

  push(samples) {
    const data = concat([this.pending, samples]);
    let at = 0;
    for (; at + FRAME <= data.length; at += FRAME) this.frame(data.slice(at, at + FRAME));
    this.pending = data.slice(at);
  }

  frame(samples) {
    const voiced = rms(samples) > 0.01;
    if (!voiced && !this.length) return;
    this.parts.push(samples);
    this.length += samples.length;
    this.quiet = voiced ? 0 : this.quiet + samples.length;
    if (this.quiet >= SILENCE || this.length >= LIMIT) this.cut();
  }

  cut() {
    if (!this.length) return;
    const audio = concat(this.parts).subarray(0, this.length - this.quiet);
    this.parts = [];
    this.length = this.quiet = 0;
    // Whistle's dictation lane uses stretches of about 20 seconds. Cut a long
    // block near quiet points and join its results before delivering anything.
    const parts = [];
    let at = 0;
    while (audio.length - at > 24 * SR) {
      const end = quietest(audio, at + 16 * SR, at + 22 * SR);
      parts.push(audio.slice(at, end));
      at = end;
    }
    parts.push(audio.slice(at));
    this.emit(parts);
  }

  finish() {
    if (this.pending.length) this.frame(this.pending);
    this.pending = new Float32Array(0);
    this.cut();
  }
}

export class Realtime {
  constructor({ engine, deliver, onState, onError }) {
    Object.assign(this, { engine, deliver, onState, onError });
    this.capture = null;
  }

  start(origin, source) {
    const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const rec = { origin, id, sequence: 0, sending: Promise.resolve(), closing: false };
    this.capture = rec;
    // Resolve model readiness and each block independently. Only delivery is
    // serialized, so recognition may finish while earlier blocks are pending.
    let ready;
    const prepare = () => ready ??= this.engine.recognition().catch((error) => {
      ready = null;
      throw error;
    });
    prepare().catch(() => {}); // warm the model while the person speaks
    const blocks = new VoiceBlocks((parts) => {
      // The workers take ownership of their buffers. Keep this block until
      // recognition settles so a failed attempt can retry without losing it.
      // Only recognition is retried here. Delivery keeps its own receipt.
      const recognize = async () => {
        const keywords = await prepare();
        return this.engine.recognize(parts.map((part) => part.slice()), keywords);
      };
      const verdict = (attempt) => attempt.then((text) => ({ text }), (error) => ({ error }));
      const result = verdict(recognize().catch(recognize));
      this.enqueue(rec, async (nonce) => {
        let { text, error } = await result;
        while (error) {
          // Keep the audio and this queue position while the person retries.
          // Later blocks may finish recognizing, but cannot be sent yet.
          this.onError(error);
          await new Promise((retry) => {
            rec.retry = retry;
            this.onState(rec);
          });
          ({ text, error } = await verdict(recognize()));
        }
        if (text) await this.deliver(origin, { text, nonce, realtime: { id: rec.id, state: rec.closing ? "closing" : "open" } });
      });
    });
    rec.blocks = blocks;
    this.onState(rec);
    rec.source = source((s) => blocks.push(s), () => this.stop()).catch((error) => {
      this.onError(error);
      return null;
    });
    rec.source.then((stop) => {
      if (!stop && !rec.closing) {
        this.capture = null;
        this.onState(null);
      }
    });
  }

  retry() {
    const rec = this.capture;
    if (!rec?.retry) return;
    const resume = rec.retry;
    rec.retry = null;
    this.onState(rec);
    resume();
  }

  enqueue(rec, send) {
    const nonce = `${rec.id}:${rec.sequence++}`;
    rec.sending = rec.sending.then(() => send(nonce)).catch((error) => this.onError(error));
  }

  async stop() {
    const rec = this.capture;
    if (!rec || rec.closing) return;
    rec.closing = true;
    this.onState(rec);
    const stop = await rec.source;
    if (stop) {
      try { await stop(); }
      catch (error) { this.onError(error); }
    }
    rec.blocks.finish();
    this.enqueue(rec, (nonce) => this.deliver(rec.origin, { nonce, realtime: { id: rec.id, state: "ended" } }));
    await rec.sending;
    this.capture = null;
    this.onState(null);
  }
}
