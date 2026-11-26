// The microphone on the audio thread: mixed to mono, posted in 128-frame blocks.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.port.onmessage = () => {
      this.port.postMessage("drained");
      this.draining = true;
    };
  }

  process(inputs) {
    if (this.draining) return true;
    const chans = inputs[0];
    if (chans.length) {
      const out = new Float32Array(chans[0].length);
      for (const c of chans) for (let i = 0; i < out.length; i++) out[i] += c[i] / chans.length;
      this.port.postMessage(out, [out.buffer]);
    }
    return true;
  }
}
registerProcessor("capture", Capture);
