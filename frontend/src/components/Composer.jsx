import { useEffect, useRef, useState } from "react";
import useHoldToTalk from "../voice/useHoldToTalk.js";
import useRecorder from "../voice/useRecorder.js";
import { CloseIcon, MicIcon, SendIcon, StopIcon, WaveIcon } from "./icons.jsx";

const MAX_HEIGHT = 200;
const DEFAULT_HINT =
  "Answers come only from your indexed documents. Enter to send, Shift+Enter for a new line.";

const HANDS_FREE_LABELS = {
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
};

const formatTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

// voice: { available, transcribing, notice, onRecorded, onStart, onError }
// handsFree: { active, status, partial, level, start, stop } from useVoiceSession
export default function Composer({ onSend, disabled, voice, handsFree }) {
  const [text, setText] = useState("");
  const ref = useRef(null);
  const stopRef = useRef(null);
  const recorder = useRecorder({ onRecorded: voice.onRecorded, onError: voice.onError });
  const recording = recorder.status === "recording";
  const busy = recording || recorder.status === "starting" || voice.transcribing || handsFree.active;

  // Grow the textarea with its content, up to MAX_HEIGHT
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [text, busy]);

  useEffect(() => {
    if (!disabled && !busy) ref.current?.focus();
  }, [disabled, busy]);

  // Esc ends hands-free
  const { active: handsFreeActive, stop: stopHandsFree } = handsFree;
  useEffect(() => {
    if (!handsFreeActive) return;
    const onKey = (e) => e.key === "Escape" && stopHandsFree();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handsFreeActive, stopHandsFree]);

  // While recording, Enter sends and Escape cancels
  useEffect(() => {
    if (recording) stopRef.current?.focus();
  }, [recording]);

  const submit = () => {
    const question = text.trim();
    if (!question || disabled) return;
    onSend(question);
    setText("");
  };

  const startRecording = () => {
    voice.onStart();
    recorder.start();
  };

  const holding = useHoldToTalk({
    recorder,
    canStart: voice.available && !disabled && !busy,
    start: startRecording,
    composerRef: ref,
  });

  const micTitle = !voice.available
    ? "Voice input is off: the backend has no DEEPGRAM_API_KEY"
    : "Ask by voice (or hold Space)";
  const idleHint = voice.available ? `${DEFAULT_HINT.slice(0, -1)}, hold Space to talk.` : DEFAULT_HINT;
  const recordingHint = holding
    ? "Speak your question. Release Space to send, Esc to cancel."
    : "Speak your question. Enter or the send button to finish, Esc to cancel.";

  let hint = voice.notice || (recording ? recordingHint : idleHint);
  if (handsFree.active && !voice.notice) {
    hint = "Hands-free: just talk. Speak over an answer to interrupt it. Esc or Stop ends it.";
  }

  return (
    <div className="composer-wrap">
      {handsFree.active ? (
        <div className={`composer recorder handsfree hf-${handsFree.status}`}>
          <span
            className="rec-dot"
            style={{ "--level": handsFree.status === "listening" ? handsFree.level : 0 }}
            aria-hidden="true"
          />
          <span className="rec-label hf-label" aria-live="polite">
            <span className="hf-status">{HANDS_FREE_LABELS[handsFree.status]}</span>
            {handsFree.partial && <span className="hf-partial">{handsFree.partial}</span>}
          </span>
          <button className="hf-stop" onClick={handsFree.stop} aria-label="Stop hands-free">
            <StopIcon /> Stop
          </button>
        </div>
      ) : busy ? (
        <div
          className={`composer recorder ${recording ? "is-recording" : ""}`}
          onKeyDown={(e) => {
            if (!recording) return;
            if (e.key === "Escape") recorder.cancel();
            if (e.key === "Enter") {
              e.preventDefault();
              recorder.stop();
            }
          }}
        >
          {recording ? (
            <>
              <span className="rec-dot" style={{ "--level": recorder.level }} aria-hidden="true" />
              <span className="rec-label">Listening…</span>
              <span className="rec-time">
                {formatTime(recorder.elapsed)} / {formatTime(recorder.maxSeconds)}
              </span>
              <button className="icon-btn rec-cancel" onClick={recorder.cancel} aria-label="Cancel recording">
                <CloseIcon />
              </button>
              <button ref={stopRef} className="send-btn" onClick={recorder.stop} aria-label="Stop and send">
                <SendIcon />
              </button>
            </>
          ) : (
            <>
              <span className="spinner" aria-hidden="true" />
              <span className="rec-label">
                {recorder.status === "starting" ? "Starting microphone…" : "Transcribing…"}
              </span>
            </>
          )}
        </div>
      ) : (
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <textarea
            ref={ref}
            rows={1}
            value={text}
            placeholder="Ask a question about your documents"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button
            className="icon-btn mic-btn"
            type="button"
            onClick={handsFree.start}
            disabled={disabled || !voice.available}
            aria-label="Start hands-free conversation"
            title={voice.available ? "Hands-free: talk back and forth, interrupt any time" : micTitle}
          >
            <WaveIcon />
          </button>
          <button
            className="icon-btn mic-btn"
            type="button"
            onClick={startRecording}
            disabled={disabled || !voice.available}
            aria-label="Ask by voice"
            title={micTitle}
          >
            <MicIcon />
          </button>
          <button className="send-btn" type="submit" disabled={disabled || !text.trim()} aria-label="Send">
            <SendIcon />
          </button>
        </form>
      )}
      <p className={`hint ${voice.notice ? "hint-notice" : ""}`} role={voice.notice ? "status" : undefined}>
        {hint}
      </p>
    </div>
  );
}
