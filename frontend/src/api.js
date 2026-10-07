// Same origin by default (Vite proxy in dev, FastAPI in prod).
// Set VITE_BACKEND_URL only if the API is hosted somewhere else.
const BASE = `${(import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "")}/api`;

// fetch() wrapper: throws an Error with the backend's `detail` message on failure.
async function send(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      // JSON bodies are strings; for FormData the browser sets the multipart header itself
      headers: typeof options.body === "string" ? { "Content-Type": "application/json" } : {},
      ...options,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("Can't reach the backend. Is it running?");
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // Non-JSON error body: keep the status text
    }
    throw new Error(detail);
  }
  return res;
}

const request = async (path, options) => (await send(path, options)).json();

export const checkHealth = () => request("/health");

export const sendChat = (question, history) =>
  request("/chat", { method: "POST", body: JSON.stringify({ question, history }) });

export const ingestDocs = () => request("/ingest", { method: "POST" });

// Fire-and-forget: re-opens the backend's Deepgram connection while the answer
// is being generated, so the spoken reply starts ~0.5 s sooner.
export const warmUpVoice = () => send("/voice/warmup", { method: "POST" }).catch(() => {});

// Returns the raw Response so the MP3 body can be played while it streams in.
export const speak = (text, signal) =>
  send("/tts", { method: "POST", body: JSON.stringify({ text }), signal });

// WebSocket URL for hands-free voice, on the same host as the API.
export function voiceSocketUrl() {
  const url = new URL(`${BASE}/ws/voice`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}

export const transcribe = (blob) => {
  const form = new FormData();
  const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
  form.append("audio", blob, `question.${ext}`);
  return request("/stt", { method: "POST", body: form });
};
