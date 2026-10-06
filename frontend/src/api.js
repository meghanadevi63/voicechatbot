// Same origin by default (Vite proxy in dev, FastAPI in prod).
// Set VITE_BACKEND_URL only if the API is hosted somewhere else.
const BASE = `${(import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "")}/api`;

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
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
  return res.json();
}

export const checkHealth = () => request("/health");

export const sendChat = (question, history) =>
  request("/chat", { method: "POST", body: JSON.stringify({ question, history }) });

export const ingestDocs = () => request("/ingest", { method: "POST" });
