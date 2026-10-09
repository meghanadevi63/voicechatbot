import { useEffect, useRef, useState } from "react";
import { appendTranscript } from "../voice/dictation.js";
import useHoldToTalk from "../voice/useHoldToTalk.js";
import useRecorder from "../voice/useRecorder.js";
import { CloseIcon, MicIcon, SendIcon, StopIcon, WaveIcon } from "./icons.jsx";

const MAX_HEIGHT = 200;
const DEFAULT_HINT =
  "Answers come only from your indexed documents. Enter to send, Shift+Enter for a new line.";
const VOICE_HINT = "Click the mic or hold Space to dictate. It isn't sent until you press Enter.";

const HANDS_FREE_LABELS = {
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
};

const formatTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

// voice: { available, notice, onStart, transcribe(blob) -> Promise<string | null>, onError }
// handsFree: { active, status, partial, level, start, stop } from useVoiceSession
export default function Composer({ onSend, disabled, voice, handsFree }) {
  const [text, setText] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const [dictated, setDictated] = useState(false); // part of the text came from the mic
  const [afterDictation, setAfterDictation] = useState(""); // hint once a transcript is added
  const ref = useRef(null);
  const caretToEnd = useRef(false);

  // Dictation: the transcript is added to the end of the box; the user sends it.
  const onRecorded = async (blob, { autoStopped }) => {
    setTranscribing(true);
    const transcript = await voice.transcribe(blob);
    setTranscribing(false);
    if (!transcript) return;
    caretToEnd.current = true;
    setText((prev) => appendTranscript(prev, transcript));
    setDictated(true);
    setAfterDictation(
      `${autoStopped ? "Stopped after a pause. " : ""}Edit if needed, then press Enter to send.`
    );
  };

  const recorder = useRecorder({ onRecorded, onError: voice.onError });
  const recording = recorder.status === "recording";
  const starting = recorder.status === "starting";
  const busy = recording || starting || transcribing;

  // Grow the textarea with its content, up to MAX_HEIGHT; after a transcript, go to its end
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
    if (caretToEnd.current) {
      caretToEnd.current = false;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      el.scrollTop = el.scrollHeight;
    }
  }, [text]);

  useEffect(() => {
    if (!disabled && !busy && !handsFree.active) ref.current?.focus();
  }, [disabled, busy, handsFree.active]);

  // Esc ends hands-free
  const { active: handsFreeActive, stop: stopHandsFree } = handsFree;
  useEffect(() => {
    if (!handsFreeActive) return;
    const onKey = (e) => e.key === "Escape" && stopHandsFree();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handsFreeActive, stopHandsFree]);

  // While recording, Enter stops (the transcript then goes into the box) and Esc cancels
  const { stop: stopRecording, cancel: cancelRecording } = recorder;
  useEffect(() => {
    if (!recording) return;
    const onKey = (e) => {
      if (e.key === "Escape") cancelRecording();
      else if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        stopRecording();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [recording, stopRecording, cancelRecording]);

  const submit = () => {
    const question = text.trim();
    if (!question || disabled || busy) return;
    onSend(question, { viaVoice: dictated });
    setText("");
    setDictated(false);
    setAfterDictation("");
  };

  const startRecording = (options) => {
    voice.onStart();
    setAfterDictation("");
    recorder.start(options);
  };

  // Holding Space means "until I let go", so it never stops at a pause
  const holding = useHoldToTalk({
    recorder,
    canStart: voice.available && !disabled && !busy,
    start: () => startRecording({ autoStop: false }),
    composerRef: ref,
  });

  const micTitle = !voice.available
    ? "Voice input is off: the backend has no DEEPGRAM_API_KEY"
    : "Dictate (or hold Space)";

  let hint = voice.available ? afterDictation || VOICE_HINT : DEFAULT_HINT;
  if (starting) hint = "Starting microphone…";
  if (recording) {
    hint = holding
      ? "Listening. Release Space to stop, Esc to cancel."
      : "Listening. Stops when you pause, or click stop / press Enter. Esc to cancel.";
  }
  if (transcribing) hint = "Transcribing…";
  if (voice.notice) hint = voice.notice;
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
      ) : (
        <form
          className={`composer ${recording ? "is-recording" : ""} ${transcribing ? "is-transcribing" : ""}`}
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <textarea
            ref={ref}
            rows={1}
            value={text}
            readOnly={busy}
            placeholder={
              recording ? "Listening…" : transcribing ? "Transcribing…" : "Ask a question about your documents"
            }
            onChange={(e) => {
              setText(e.target.value);
              if (!e.target.value.trim()) setDictated(false);
              setAfterDictation("");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />
          {recording && (
            <>
              <span className="rec-time">{formatTime(recorder.elapsed)}</span>
              <button
                className="icon-btn rec-cancel"
                type="button"
                onClick={recorder.cancel}
                aria-label="Cancel recording"
                title="Cancel (Esc)"
              >
                <CloseIcon />
              </button>
            </>
          )}
          <button
            className="icon-btn mic-btn"
            type="button"
            onClick={handsFree.start}
            disabled={disabled || busy || !voice.available}
            aria-label="Start hands-free conversation"
            title={voice.available ? "Hands-free: talk back and forth, interrupt any time" : micTitle}
          >
            <WaveIcon />
          </button>
          {recording ? (
            <button
              className="icon-btn mic-btn mic-stop"
              type="button"
              onClick={recorder.stop}
              style={{ "--level": recorder.level }}
              aria-label="Stop dictating"
              title="Stop (Enter)"
            >
              <StopIcon />
            </button>
          ) : (
            <button
              className="icon-btn mic-btn"
              type="button"
              onClick={() => startRecording()}
              disabled={disabled || busy || !voice.available}
              aria-label={transcribing ? "Transcribing" : "Dictate"}
              title={micTitle}
            >
              {transcribing || starting ? <span className="spinner" aria-hidden="true" /> : <MicIcon />}
            </button>
          )}
          <button className="send-btn" type="submit" disabled={disabled || busy || !text.trim()} aria-label="Send">
            <SendIcon />
          </button>
        </form>
      )}
      <p className={`hint ${voice.notice ? "hint-notice" : ""}`} aria-live="polite">
        {hint}
      </p>
    </div>
  );
}
