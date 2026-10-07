// Hands-free reply playback: queues PCM16 mono chunks (at the context's rate,
// 24 kHz) and plays them back to back.
//
// Messages in:  ArrayBuffer   PCM16 audio to queue
//               "end"         no more audio for this reply
//               "flush"       barge-in: drop everything now
// Messages out: "ended"       an ended reply has finished playing (also sent
//                             right away if "end" comes with nothing queued)

// Wait for 150 ms of audio before starting, so small gaps in the stream
// (Aura-2 sends with gaps up to ~70 ms) don't cause clicks.
const PREBUFFER = sampleRate * 0.15;

class PlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.reset();
    this.port.onmessage = ({ data }) => {
      if (data === "flush") {
        this.reset();
      } else if (data === "end") {
        this.ending = true;
        if (this.buffered === 0) this.finish();
      } else {
        const pcm = new Int16Array(data);
        const samples = new Float32Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000;
        this.queue.push(samples);
        this.buffered += samples.length;
      }
    };
  }

  reset() {
    this.queue = [];
    this.offset = 0;
    this.buffered = 0;
    this.playing = false;
    this.ending = false;
  }

  finish() {
    this.reset();
    this.port.postMessage("ended");
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.playing && this.buffered > 0 && (this.buffered >= PREBUFFER || this.ending)) {
      this.playing = true;
    }
    if (!this.playing) return true;

    let i = 0;
    while (i < out.length && this.queue.length) {
      const chunk = this.queue[0];
      const n = Math.min(out.length - i, chunk.length - this.offset);
      out.set(chunk.subarray(this.offset, this.offset + n), i);
      i += n;
      this.offset += n;
      this.buffered -= n;
      if (this.offset === chunk.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    for (let ch = 1; ch < outputs[0].length; ch++) outputs[0][ch].set(out);

    if (this.buffered === 0) {
      if (this.ending) this.finish();
      else this.playing = false; // ran dry mid-reply: buffer up again before resuming
    }
    return true;
  }
}

registerProcessor("player-processor", PlayerProcessor);
