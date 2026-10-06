import { useEffect, useRef, useState } from "react";
import useRecorder from "../voice/useRecorder.js";
import { CloseIcon, MicIcon, SendIcon } from "./icons.jsx";

const MAX_HEIGHT = 200;
const DEFAULT_HINT =
  "Answers come only from your indexed documents. Enter to send, Shift+Enter for a new line.";

const formatTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

// voice: { available, transcribing, notice, onRecorded, onStart, onError }
export default function Composer({ onSend, disabled, voice }) {
  const [text, setText] = useState("");
  const ref = useRef(null);
  const stopRef = useRef(null);
  const recorder = useRecorder({ onRecorded: voice.onRecorded, onError: voice.onError });
  const recording = recorder.status === "recording";
  const busy = recording || recorder.status === "starting" || voice.transcribing;

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

  const micTitle = !voice.available
    ? "Voice input is off: the backend has no DEEPGRAM_API_KEY"
    : "Ask by voice";

  return (
    <div className="composer-wrap">
      {busy ? (
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
        {voice.notice ||
          (recording ? "Speak your question. Enter or the send button to finish, Esc to cancel." : DEFAULT_HINT)}
      </p>
    </div>
  );
}
