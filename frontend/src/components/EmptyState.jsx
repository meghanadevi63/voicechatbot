import { LogoMark } from "./icons.jsx";

const SUGGESTIONS = [
  "What topics do these documents cover?",
  "Summarize the main ideas in a few sentences",
  "What are the key takeaways?",
  "Give me a practical example from the documents",
];

export default function EmptyState({ onPick, disabled }) {
  return (
    <div className="empty">
      <LogoMark />
      <h1>Ask your documents</h1>
      <p className="muted">Answers are grounded in the PDFs you've indexed, with sources you can check.</p>
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="suggestion" onClick={() => onPick(s)} disabled={disabled}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
