import Sources from "./Sources.jsx";

export default function ChatMessage({ message }) {
  const { role, content, sources, error, typing } = message;

  if (role === "user") {
    return (
      <div className="msg msg-user">
        <div className="bubble">{content}</div>
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
          {sources?.length > 0 && <Sources sources={sources} />}
        </>
      )}
    </div>
  );
}
