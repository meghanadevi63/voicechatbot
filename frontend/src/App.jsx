import { useCallback, useEffect, useRef, useState } from "react";
import { checkHealth, ingestDocs, sendChat, transcribe, warmUpVoice } from "./api.js";
import ChatMessage from "./components/ChatMessage.jsx";
import Composer from "./components/Composer.jsx";
import EmptyState from "./components/EmptyState.jsx";
import Sidebar from "./components/Sidebar.jsx";
import { MenuIcon } from "./components/icons.jsx";
import usePlayer from "./voice/usePlayer.js";
import useVoiceSession from "./voice/useVoiceSession.js";

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
  const [voiceAvailable, setVoiceAvailable] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voiceNotice, setVoiceNotice] = useState("");
  const [ingest, setIngest] = useState({ status: "idle" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(loadSpeakReplies);
  const player = usePlayer();
  const { play: playSpeech, stop: stopSpeech } = player;
  const bottomRef = useRef(null);

  // Hands-free: server events stream into the same message list as typed questions.
  const answerId = useRef(null); // assistant message being filled by answer_* events
  const spokenId = useRef(null); // assistant message whose audio is playing
  const updateMessage = (id, change) =>
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...change(m) } : m)));

  const onVoiceEvent = useCallback((event) => {
    switch (event.type) {
      case "user_turn":
        setMessages((prev) => [...prev, { id: newId(), role: "user", content: event.text, viaVoice: true }]);
        break;
      case "answer_sources": {
        const id = newId();
        answerId.current = id;
        spokenId.current = id;
        setMessages((prev) => [...prev, { id, role: "assistant", content: "", sources: event.sources }]);
        break;
      }
      case "answer_delta":
        updateMessage(answerId.current, (m) => ({ content: m.content + event.text }));
        break;
      case "answer_done": {
        const id = answerId.current;
        answerId.current = null;
        if (id) updateMessage(id, () => ({ note: event.error, interrupted: event.interrupted }));
        else if (event.error) {
          setMessages((prev) => [...prev, { id: newId(), role: "assistant", content: event.error, error: true }]);
        }
        break;
      }
      case "stop_playback":
        if (spokenId.current) updateMessage(spokenId.current, () => ({ interrupted: true }));
        spokenId.current = null;
        break;
      case "error":
        setVoiceNotice(event.message);
        break;
      case "idle_stop":
        setVoiceNotice("Hands-free stopped after 2 minutes of silence.");
        break;
    }
  }, []);
  const handsFree = useVoiceSession({ onEvent: onVoiceEvent });
  const handsFreeThinking = handsFree.status === "thinking" && messages.at(-1)?.role === "user";

  useEffect(() => {
    if (handsFree.status === "listening" || handsFree.status === "off") spokenId.current = null;
  }, [handsFree.status]);

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
        .then((h) => {
          if (!active) return;
          setBackend("ok");
          setVoiceAvailable(Boolean(h.voice));
        })
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
  }, [messages, pending, handsFreeThinking]);

  const ask = useCallback(
    async (question, { viaVoice = false } = {}) => {
      const history = toHistory(messages);
      stopSpeech(); // a new question interrupts the previous spoken reply
      setVoiceNotice("");
      setMessages((prev) => [...prev, { id: newId(), role: "user", content: question, viaVoice }]);
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

  const askByVoice = useCallback(
    async (blob) => {
      setTranscribing(true);
      let transcript;
      try {
        ({ transcript } = await transcribe(blob));
      } catch (e) {
        setVoiceNotice(`Couldn't transcribe: ${e.message}`);
        return;
      } finally {
        setTranscribing(false);
      }
      if (!transcript) {
        setVoiceNotice("Sorry, I didn't catch that. Try again, a little closer to the mic.");
        return;
      }
      ask(transcript, { viaVoice: true });
    },
    [ask]
  );

  const voice = {
    available: voiceAvailable && backend === "ok",
    transcribing,
    notice: voiceNotice,
    onStart: () => {
      stopSpeech(); // don't record the bot's own voice
      setVoiceNotice("");
      warmUpVoice(); // the Deepgram connection is ready when the recording is sent
    },
    onRecorded: askByVoice,
    onError: setVoiceNotice,
  };

  const startHandsFree = useCallback(() => {
    stopSpeech();
    setVoiceNotice("");
    handsFree.start(toHistory(messages));
  }, [handsFree, messages, stopSpeech]);

  const reingest = useCallback(async () => {
    setIngest({ status: "running" });
    try {
      setIngest({ status: "done", result: await ingestDocs() });
    } catch (e) {
      setIngest({ status: "error", message: e.message });
    }
  }, []);

  const { stop: stopHandsFree } = handsFree;
  const clearChat = useCallback(() => {
    stopSpeech();
    stopHandsFree(); // its server-side history would still hold the old chat
    setMessages([]);
    setSidebarOpen(false);
  }, [stopSpeech, stopHandsFree]);
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
            {messages.length === 0 && !transcribing ? (
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
            {transcribing && <ChatMessage message={{ role: "user", typing: true }} />}
            {(pending || handsFreeThinking) && <ChatMessage message={{ role: "assistant", typing: true }} />}
            <div ref={bottomRef} />
          </div>
        </div>

        <Composer
          onSend={ask}
          disabled={pending}
          voice={voice}
          handsFree={{ ...handsFree, start: startHandsFree }}
        />
      </main>
    </div>
  );
}
