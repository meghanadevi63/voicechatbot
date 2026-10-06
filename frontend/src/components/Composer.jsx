import { useEffect, useRef, useState } from "react";
import { SendIcon } from "./icons.jsx";

const MAX_HEIGHT = 200;

export default function Composer({ onSend, disabled }) {
  const [text, setText] = useState("");
  const ref = useRef(null);

  // Grow the textarea with its content, up to MAX_HEIGHT
  useEffect(() => {
    const el = ref.current;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [text]);

  useEffect(() => {
    if (!disabled) ref.current?.focus();
  }, [disabled]);

  const submit = () => {
    const question = text.trim();
    if (!question || disabled) return;
    onSend(question);
    setText("");
  };

  return (
    <div className="composer-wrap">
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
        <button className="send-btn" type="submit" disabled={disabled || !text.trim()} aria-label="Send">
          <SendIcon />
        </button>
      </form>
      <p className="hint">Answers come only from your indexed documents. Enter to send, Shift+Enter for a new line.</p>
    </div>
  );
}
