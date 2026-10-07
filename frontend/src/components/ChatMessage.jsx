import Sources from "./Sources.jsx";
import { MicIcon, SpeakerIcon, StopIcon } from "./icons.jsx";

// speech: "loading" | "playing" | null for this message
function SpeechButton({ speech, onSpeak, onStop }) {
  if (speech) {
    return (
      <button className="speech-btn active" onClick={onStop} aria-label="Stop speaking">
        {speech === "loading" ? <span className="spinner" /> : <StopIcon />}
        {speech === "loading" ? "Loading audio…" : "Stop"}
      </button>
    );
  }
  return (
    <button className="speech-btn" onClick={onSpeak} aria-label="Read answer aloud">
      <SpeakerIcon /> Listen
    </button>
  );
}

export default function ChatMessage({ message, speech, speechError, onSpeak, onStopSpeech }) {
  const { role, content, sources, error, typing, viaVoice } = message;

  if (role === "user") {
    return (
      <div className="msg msg-user">
        <div className="bubble">
          {typing ? (
            <span className="typing typing-light" aria-label="Transcribing">
              <span />
              <span />
              <span />
            </span>
          ) : (
            content
          )}
        </div>
        {viaVoice && (
          <span className="via-voice">
            <MicIcon /> Asked by voice
          </span>
        )}
      </div>
    );
  }

  return (
    <div className={`msg msg-assistant ${error ? "msg-error" : ""}`}>
      {typing ? (
        <div className="typing" aria-label="Assistant is thinking">
          <span />
          <span />
          <span />
        </div>
      ) : (
        <>
          <div className="answer">{error ? `Something went wrong: ${content}` : content}</div>
          {!error && onSpeak && (
            <div className="msg-actions">
              <SpeechButton speech={speech} onSpeak={onSpeak} onStop={onStopSpeech} />
              {message.interrupted && <span className="msg-note">Interrupted</span>}
              {(speechError || message.note) && <span className="speech-error">{speechError || message.note}</span>}
            </div>
          )}
          {sources?.length > 0 && <Sources sources={sources} />}
        </>
      )}
    </div>
  );
}
