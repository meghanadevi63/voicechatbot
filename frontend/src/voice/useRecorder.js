import { useCallback, useEffect, useRef, useState } from "react";

const MAX_SECONDS = 60;
const MIN_SECONDS = 0.5;
// Chrome/Firefox record WebM/Opus, Safari MP4/AAC; Deepgram accepts both.
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

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

/**
 * Push-to-talk recorder.
 * status: "idle" | "starting" | "recording"
 * `onRecorded(blob)` is called when a recording is stopped (by stop() or the 60 s limit).
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
    cancelAnimationFrame(s.raf);
    s.stream.getTracks().forEach((t) => t.stop()); // turns off the browser's mic indicator
    s.audioContext?.close();
    setStatus("idle");
    setElapsed(0);
    setLevel(0);
  }, []);

  // keep: true -> hand the recording to onRecorded; false -> discard it
  const finish = useCallback(
    (keep) => {
      const s = session.current;
      if (!s || s.recorder.state === "inactive") return;
      s.keep = keep;
      s.recorder.stop(); // fires "stop" once the last data has been delivered
    },
    []
  );

  const start = useCallback(async () => {
    if (session.current) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      callbacks.current.onError?.(
        window.isSecureContext
          ? "This browser can't record audio."
          : "The microphone needs HTTPS (or localhost)."
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
    const s = { stream, recorder, keep: false, startedAt: performance.now() };
    session.current = s;

    recorder.addEventListener("dataavailable", (e) => e.data.size && chunks.push(e.data));
    recorder.addEventListener("stop", () => {
      const seconds = (performance.now() - s.startedAt) / 1000;
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
      cleanup();
      if (!s.keep) return;
      if (seconds < MIN_SECONDS || !blob.size) {
        callbacks.current.onError?.("That was too short. Click the mic, speak, then click send.");
        return;
      }
      callbacks.current.onRecorded?.(blob);
    });

    // Elapsed time + auto-stop at the limit
    s.timer = setInterval(() => {
      const seconds = (performance.now() - s.startedAt) / 1000;
      setElapsed(Math.floor(seconds));
      if (seconds >= MAX_SECONDS) finish(true);
    }, 250);

    // Input level meter (purely visual; recording works without it)
    try {
      s.audioContext = new AudioContext();
      const analyser = s.audioContext.createAnalyser();
      analyser.fftSize = 512;
      s.audioContext.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const v of data) sum += ((v - 128) / 128) ** 2;
        setLevel(Math.min(1, Math.sqrt(sum / data.length) * 4));
        s.raf = requestAnimationFrame(tick);
      };
      tick();
    } catch {
      // No meter
    }

    recorder.start();
    setStatus("recording");
  }, [cleanup, finish]);

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

  return { status, elapsed, level, maxSeconds: MAX_SECONDS, start, stop, cancel };
}
