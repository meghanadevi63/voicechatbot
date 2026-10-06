import { useCallback, useEffect, useRef, useState } from "react";
import { checkHealth, ingestDocs, sendChat, warmUpVoice } from "./api.js";
import ChatMessage from "./components/ChatMessage.jsx";
import Composer from "./components/Composer.jsx";
import EmptyState from "./components/EmptyState.jsx";
import Sidebar from "./components/Sidebar.jsx";
import { MenuIcon } from "./components/icons.jsx";
import usePlayer from "./voice/usePlayer.js";

const STORAGE_KEY = "docs-assistant-messages";
const SPEAK_KEY = "docs-assistant-speak-replies";
const HEALTH_INTERVAL_MS = 15000;

let nextId = 0;
const newId = () => `${Date.now()}-${nextId++}`;

function loadMessages() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch {
    return [];
  }
}

function loadSpeakReplies() {
  try {
    return localStorage.getItem(SPEAK_KEY) !== "false"; // on by default
  } catch {
    return true;
  }
}

// History sent to the backend: skip failed replies and the question that caused them.
function toHistory(messages) {
  return messages
    .filter((m, i) => !m.error && !messages[i + 1]?.error)
    .map(({ role, content }) => ({ role, content }));
}

export default function App() {
  const [messages, setMessages] = useState(loadMessages);
  const [pending, setPending] = useState(false);
  const [backend, setBackend] = useState("checking");
  const [ingest, setIngest] = useState({ status: "idle" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(loadSpeakReplies);
  const player = usePlayer();
  const { play: playSpeech, stop: stopSpeech } = player;
  const bottomRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(SPEAK_KEY, String(speakReplies));
    } catch {
      // Storage unavailable: the setting just isn't remembered
    }
  }, [speakReplies]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // Storage unavailable (private mode etc.): chat still works, just isn't kept
    }
  }, [messages]);

  useEffect(() => {
    let active = true;
    const check = () =>
      checkHealth()
        .then(() => active && setBackend("ok"))
        .catch(() => active && setBackend("down"));
    check();
    const timer = setInterval(check, HEALTH_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  const ask = useCallback(
    async (question) => {
      const history = toHistory(messages);
      stopSpeech(); // a new question interrupts the previous spoken reply
      setMessages((prev) => [...prev, { id: newId(), role: "user", content: question }]);
      setPending(true);
      if (speakReplies) warmUpVoice();
      try {
        const { answer, sources } = await sendChat(question, history);
        const id = newId();
        setMessages((prev) => [...prev, { id, role: "assistant", content: answer, sources }]);
        if (speakReplies) playSpeech(id, answer);
      } catch (e) {
        setMessages((prev) => [
          ...prev,
          { id: newId(), role: "assistant", content: e.message, error: true },
        ]);
      } finally {
        setPending(false);
      }
    },
    [messages, speakReplies, playSpeech, stopSpeech]
  );

  const reingest = useCallback(async () => {
    setIngest({ status: "running" });
    try {
      setIngest({ status: "done", result: await ingestDocs() });
    } catch (e) {
      setIngest({ status: "error", message: e.message });
    }
  }, []);

  const clearChat = useCallback(() => {
    stopSpeech();
    setMessages([]);
    setSidebarOpen(false);
  }, [stopSpeech]);

  const toggleSpeakReplies = useCallback(() => {
    if (speakReplies) stopSpeech();
    setSpeakReplies(!speakReplies);
  }, [speakReplies, stopSpeech]);

  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        backend={backend}
        ingest={ingest}
        onIngest={reingest}
        onClear={clearChat}
        canClear={messages.length > 0 && !pending}
        speakReplies={speakReplies}
        onToggleSpeakReplies={toggleSpeakReplies}
      />

      <main className="main">
        <header className="topbar">
          <button className="icon-btn" onClick={() => setSidebarOpen(true)} aria-label="Open menu">
            <MenuIcon />
          </button>
          <span className="topbar-title">Docs Assistant</span>
        </header>

        <div className="messages">
          <div className="messages-inner">
            {messages.length === 0 ? (
              <EmptyState onPick={ask} disabled={pending} />
            ) : (
              messages.map((m) => (
                <ChatMessage
                  key={m.id}
                  message={m}
                  speech={player.speech?.id === m.id ? player.speech.status : null}
                  speechError={player.error?.id === m.id ? player.error.message : null}
                  onSpeak={() => playSpeech(m.id, m.content)}
                  onStopSpeech={stopSpeech}
                />
              ))
            )}
            {pending && <ChatMessage message={{ role: "assistant", typing: true }} />}
            <div ref={bottomRef} />
          </div>
        </div>

        <Composer onSend={ask} disabled={pending} />
      </main>
    </div>
  );
}
