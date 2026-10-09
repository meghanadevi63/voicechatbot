import { useCallback, useEffect, useRef, useState } from "react";

const MIN_SECONDS = 0.5;
// Chrome/Firefox record WebM/Opus, Safari MP4/AAC; Deepgram accepts both.
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

// Auto-stop. The check runs on an interval, not requestAnimationFrame: browsers
// pause animation frames in background tabs, which would leave the mic on.
const CHECK_MS = 50;
const SILENCE_MS = 3000; // quiet this long after speaking: stop and transcribe
const NO_SPEECH_MS = 15000; // nothing said this long after starting: stop and discard
// Speech means MIN_SPEECH_MS of loud input without a gap over SPEECH_GAP_MS,
// so a click or a cough (even repeated ones) isn't speech.
const MIN_SPEECH_MS = 200;
const SPEECH_GAP_MS = 300;
// Speech is louder than both SPEECH_LEVEL and NOISE_RATIO x the room's noise floor,
// so a fan that auto-gain turns up doesn't count as talking.
const SPEECH_LEVEL = 0.1;
const NOISE_RATIO = 2.5;
const FLOOR_RISE = 0.003; // the floor drops at once but rises slowly (~15 s), so speech barely moves it

function pickMimeType() {
  return MIME_TYPES.find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || "";
}

function micErrorMessage(e) {
  if (!window.isSecureContext) return "The microphone needs HTTPS (or localhost).";
  switch (e?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone blocked. Allow it in the browser's site settings, then try again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone found.";
    case "NotReadableError":
      return "The microphone is in use by another app.";
    default:
      return `Couldn't start the microphone: ${e?.message || e}`;
  }
}

// Input volume, roughly 0 (silence) .. 1 (loud speech)
function readLevel(analyser, data) {
  analyser.getByteTimeDomainData(data);
  let sum = 0;
  for (const v of data) sum += ((v - 128) / 128) ** 2;
  return Math.sqrt(sum / data.length) * 4;
}

/**
 * Push-to-talk recorder. Records until stop(), or until it hears a pause:
 * SILENCE_MS of quiet after speech stops and keeps the recording; NO_SPEECH_MS
 * without any speech discards it. start({ autoStop: false }) turns both off.
 *
 * status: "idle" | "starting" | "recording"
 * `onRecorded(blob, { autoStopped })` is called with each kept recording.
 * `level` (0..1) is the current input volume, for a simple meter.
 */
export default function useRecorder({ onRecorded, onError }) {
  const [status, setStatus] = useState("idle");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const session = useRef(null);
  const callbacks = useRef({ onRecorded, onError });
  callbacks.current = { onRecorded, onError };

  const cleanup = useCallback(() => {
    const s = session.current;
    session.current = null;
    if (!s) return;
    clearInterval(s.timer);
    clearInterval(s.vad);
    cancelAnimationFrame(s.raf);
    s.stream.getTracks().forEach((t) => t.stop()); // turns off the browser's mic indicator
    s.audioContext?.close();
    setStatus("idle");
    setElapsed(0);
    setLevel(0);
  }, []);

  // keep: true -> hand the recording to onRecorded; false -> discard it
  const finish = useCallback((keep, autoStopped = false) => {
    const s = session.current;
    if (!s || s.recorder.state === "inactive") return;
    clearInterval(s.vad);
    s.keep = keep;
    s.autoStopped = autoStopped;
    s.recorder.stop(); // fires "stop" once the last data has been delivered
  }, []);

  // Watch the level and stop at a pause. Time only counts while the AudioContext
  // runs: a suspended one reads as silence and must not end a recording.
  const watchForPause = useCallback(
    (s, analyser) => {
      const data = new Uint8Array(analyser.fftSize);
      const vad = { floor: null, heard: false, speechMs: 0, quietMs: 0, activeMs: 0, last: performance.now() };
      s.vad = setInterval(() => {
        const now = performance.now();
        const dt = now - vad.last;
        vad.last = now;
        if (s.audioContext.state !== "running") return;

        const level = readLevel(analyser, data);
        if (level > 0) {
          vad.floor = vad.floor === null || level < vad.floor ? level : vad.floor + (level - vad.floor) * FLOOR_RISE;
        }
        const loud = level > Math.max(SPEECH_LEVEL, (vad.floor ?? 0) * NOISE_RATIO);
        vad.activeMs += dt;
        if (loud) {
          vad.speechMs += dt;
          vad.quietMs = 0;
          if (vad.speechMs >= MIN_SPEECH_MS) vad.heard = true;
        } else {
          vad.quietMs += dt;
          if (vad.quietMs > SPEECH_GAP_MS) vad.speechMs = 0;
        }

        if (vad.heard) {
          if (vad.quietMs >= SILENCE_MS) finish(true, true);
        } else if (vad.activeMs >= NO_SPEECH_MS) {
          finish(false);
          callbacks.current.onError?.("Didn't hear anything. Click the mic and try again.");
        }
      }, CHECK_MS);
    },
    [finish]
  );

  const start = useCallback(
    async ({ autoStop = true } = {}) => {
      if (session.current) return;
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        callbacks.current.onError?.(
          window.isSecureContext ? "This browser can't record audio." : "The microphone needs HTTPS (or localhost)."
        );
        return;
      }
      setStatus("starting");
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch (e) {
        setStatus("idle");
        callbacks.current.onError?.(micErrorMessage(e));
        return;
      }

      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks = [];
      const s = { stream, recorder, keep: false, autoStopped: false, startedAt: performance.now() };
      session.current = s;

      recorder.addEventListener("dataavailable", (e) => e.data.size && chunks.push(e.data));
      recorder.addEventListener("stop", () => {
        const seconds = (performance.now() - s.startedAt) / 1000;
        const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
        cleanup();
        if (!s.keep) return;
        if (seconds < MIN_SECONDS || !blob.size) {
          callbacks.current.onError?.("That was too short. Speak, then stop (or release Space).");
          return;
        }
        callbacks.current.onRecorded?.(blob, { autoStopped: s.autoStopped });
      });

      s.timer = setInterval(() => setElapsed(Math.floor((performance.now() - s.startedAt) / 1000)), 250);

      // Level meter and pause detection. Without them, recording still works
      // and only stops when asked to.
      try {
        s.audioContext = new AudioContext();
        if (s.audioContext.state === "suspended") s.audioContext.resume().catch(() => {});
        const analyser = s.audioContext.createAnalyser();
        analyser.fftSize = 2048; // ~43 ms at 48 kHz: enough to span a syllable
        s.audioContext.createMediaStreamSource(stream).connect(analyser);
        const data = new Uint8Array(analyser.fftSize);
        const tick = () => {
          setLevel(Math.min(1, readLevel(analyser, data)));
          s.raf = requestAnimationFrame(tick);
        };
        tick();
        if (autoStop) watchForPause(s, analyser);
      } catch {
        // No meter, no auto-stop
      }

      recorder.start();
      setStatus("recording");
    },
    [cleanup, watchForPause]
  );

  const stop = useCallback(() => finish(true), [finish]);
  const cancel = useCallback(() => finish(false), [finish]);

  // Release the mic if the component unmounts mid-recording
  useEffect(
    () => () => {
      if (session.current) session.current.keep = false;
      session.current?.recorder.state !== "inactive" && session.current?.recorder.stop();
      cleanup();
    },
    [cleanup]
  );

  return { status, elapsed, level, start, stop, cancel };
}
