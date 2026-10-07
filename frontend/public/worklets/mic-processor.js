// Hands-free mic capture: downmix to mono, resample to 16 kHz and post PCM16
// frames of 80 ms (what Deepgram Flux expects).
//
// Resampling happens here instead of by creating a 16 kHz AudioContext:
// Firefox can't connect a microphone to a context running at another rate.
//
// Posts { frame: ArrayBuffer (1280 x Int16), level: 0..1 } per frame.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = TARGET_RATE * 0.08;

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE; // input samples per output sample
    this.pos = 0; // position of the next output sample in the current block (-1 = last sample of the previous block)
    this.prev = 0;
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.energy = 0;
  }

  process(inputs) {
    const channels = inputs[0];
    if (!channels || channels.length === 0) return true;
    const n = channels[0].length;

    const mono = new Float32Array(n);
    for (const ch of channels) {
      for (let i = 0; i < n; i++) mono[i] += ch[i] / channels.length;
    }

    // Linear interpolation. Speech has little energy above 8 kHz, so the
    // missing low-pass filter doesn't hurt recognition.
    while (this.pos < n - 1) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const a = i < 0 ? this.prev : mono[i];
      const sample = a + (mono[i + 1] - a) * frac;
      this.push(sample);
      this.pos += this.step;
    }
    this.pos -= n;
    this.prev = mono[n - 1];
    return true;
  }

  push(sample) {
    const s = Math.max(-1, Math.min(1, sample));
    this.frame[this.filled++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    this.energy += s * s;
    if (this.filled === FRAME_SAMPLES) {
      const level = Math.min(1, Math.sqrt(this.energy / FRAME_SAMPLES) * 4);
      const buffer = this.frame.buffer;
      this.port.postMessage({ frame: buffer, level }, [buffer]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.filled = 0;
      this.energy = 0;
    }
  }
}

registerProcessor("mic-processor", MicProcessor);
