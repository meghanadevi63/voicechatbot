import { useCallback, useEffect, useRef, useState } from "react";
import { voiceSocketUrl } from "../api.js";

const WORKLETS = `${import.meta.env.BASE_URL}worklets/`;
const REPLY_RATE = 24000; // the server sends PCM16 mono at 24 kHz
// Hands-free streams paid STT the whole time it's on: stop after this much quiet.
const IDLE_STOP_MS = 2 * 60 * 1000;
// Mic audio kept while the server connects to Deepgram (80 ms frames: 10 s)
const MAX_EARLY_FRAMES = 125;

function micErrorMessage(e) {
  if (!window.isSecureContext) return "The microphone needs HTTPS (or localhost).";
  if (e?.name === "NotAllowedError" || e?.name === "SecurityError")
    return "Microphone blocked. Allow it in the browser's site settings, then try again.";
  if (e?.name === "NotFoundError") return "No microphone found.";
  if (e?.name === "NotReadableError") return "The microphone is in use by another app.";
  return `Couldn't start hands-free voice: ${e?.message || e}`;
}

/**
 * Hands-free voice session over /api/ws/voice (protocol: backend/voice_session.py).
 *
 * status: "off" | "connecting" | "listening" | "thinking" | "speaking"
 * `onEvent(event)` receives the server's user_turn, answer_sources, answer_delta,
 * answer_done and stop_playback events, plus { type: "error", message } when
 * the session ends because of a problem, and { type: "idle_stop" }.
 */
export default function useVoiceSession({ onEvent }) {
  const [status, setStatus] = useState("off");
  const [partial, setPartial] = useState("");
  const [level, setLevel] = useState(0);
  const session = useRef(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const cleanup = useCallback(() => {
    const s = session.current;
    session.current = null;
    if (!s) return;
    s.closed = true;
    clearInterval(s.idleTimer);
    if (s.ws && s.ws.readyState <= WebSocket.OPEN) {
      if (s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ type: "stop" }));
      s.ws.close();
    }
    s.stream?.getTracks().forEach((t) => t.stop());
    s.micCtx?.close();
    s.playCtx?.close();
    setStatus("off");
    setPartial("");
    setLevel(0);
  }, []);

  const fail = useCallback(
    (message) => {
      cleanup();
      onEventRef.current?.({ type: "error", message });
    },
    [cleanup]
  );

  const start = useCallback(
    async (history) => {
      if (session.current) return;
      const s = { closed: false, ready: false, lastActivity: Date.now() };
      session.current = s;
      setStatus("connecting");
      const isCurrent = () => session.current === s;

      try {
        // Both contexts are created inside the click, so the browser lets them play.
        s.micCtx = new AudioContext();
        s.playCtx = new AudioContext({ sampleRate: REPLY_RATE });
        s.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        });
        await Promise.all([
          s.micCtx.audioWorklet.addModule(`${WORKLETS}mic-processor.js`),
          s.playCtx.audioWorklet.addModule(`${WORKLETS}player-processor.js`),
        ]);
      } catch (e) {
        if (isCurrent()) fail(micErrorMessage(e));
        return;
      }
      if (!isCurrent()) return;

      // No outputs: the node is processed as long as the mic feeds it.
      s.mic = new AudioWorkletNode(s.micCtx, "mic-processor", { numberOfOutputs: 0 });
      s.micCtx.createMediaStreamSource(s.stream).connect(s.mic);
      s.player = new AudioWorkletNode(s.playCtx, "player-processor", { outputChannelCount: [1] });
      s.player.connect(s.playCtx.destination);

      const ws = new WebSocket(voiceSocketUrl());
      s.ws = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => ws.send(JSON.stringify({ type: "start", history }));
      ws.onclose = () => {
        if (!s.closed) fail("The voice connection was lost. Hands-free has stopped.");
      };

      // The server needs a few seconds to open Deepgram before it listens. Keep
      // what the user says meanwhile and send it once it's ready, so an early
      // start isn't lost.
      s.early = [];
      s.mic.port.onmessage = ({ data }) => {
        setLevel(data.level);
        if (!s.ready) {
          if (s.early.length < MAX_EARLY_FRAMES) s.early.push(data.frame);
        } else if (ws.readyState === WebSocket.OPEN) {
          ws.send(data.frame);
        }
      };
      s.player.port.onmessage = ({ data }) => {
        if (data === "ended" && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "playback_done" }));
        }
      };

      ws.onmessage = ({ data }) => {
        if (typeof data !== "string") {
          s.player.port.postMessage(data, [data]);
          return;
        }
        const event = JSON.parse(data);
        switch (event.type) {
          case "status":
            if (!s.ready) {
              s.ready = true;
              s.early.forEach((frame) => ws.send(frame));
              s.early = [];
            }
            s.lastActivity = Date.now();
            setStatus(event.status);
            if (event.status === "listening") setPartial(""); // e.g. after "Stop."
            return;
          case "partial_transcript":
            s.lastActivity = Date.now();
            setPartial(event.text);
            return;
          case "user_turn":
            setPartial("");
            break;
          case "answer_done":
            s.player.port.postMessage("end");
            break;
          case "stop_playback":
            s.player.port.postMessage("flush");
            break;
          case "error":
            fail(event.message);
            return;
        }
        onEventRef.current?.(event);
      };

      s.idleTimer = setInterval(() => {
        if (session.current === s && s.ready && Date.now() - s.lastActivity > IDLE_STOP_MS) {
          cleanup();
          onEventRef.current?.({ type: "idle_stop" });
        }
      }, 5000);
    },
    [cleanup, fail]
  );

  useEffect(() => cleanup, [cleanup]);

  return { status, partial, level, active: status !== "off", start, stop: cleanup };
}
