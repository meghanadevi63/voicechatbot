# RAG Chatbot (React + FastAPI + LangChain + Milvus Lite)

Ask questions about the PDFs in `docs/`. Answers come from Groq, grounded in chunks retrieved from a local Milvus Lite database (`data/milvus.db` — no Docker or server needed).

```
React (frontend/)   ──HTTP──▶ FastAPI (backend/main.py) ──▶ RAG chain (backend/rag.py) ──▶ Groq LLM
                                                       │
                                                       ▼
                                Milvus Lite file ◀── backend/ingest.py ◀── docs/*.pdf
                                (HuggingFace embeddings: all-MiniLM-L6-v2)
```

## Setup

The system `python3.11` on this machine is 3.11.0rc1, which breaks torch. Use a uv-managed Python 3.11 instead:

```bash
uv python install 3.11
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r requirements.txt --index-strategy unsafe-best-match

cp .env.example .env    # then set GROQ_API_KEY (and DEEPGRAM_API_KEY for voice)
```

Frontend (Node 20+):

```bash
cd frontend
npm install
```

## Run

```bash
# 1. Backend (port 8001)
uvicorn backend.main:app --port 8001

# 2. Index the docs (first run, or after adding PDFs) — or use "Re-ingest documents" in the UI sidebar
curl -X POST localhost:8001/api/ingest

# 3. Frontend, development (hot reload): http://localhost:5173
cd frontend && npm run dev
```

All API routes are under `/api`. The Vite dev server forwards `/api` (including WebSockets) to the backend, so no CORS setup is needed. If the frontend is ever hosted on a different domain from the API, build it with `VITE_BACKEND_URL=https://api.example.com` and set `CORS_ORIGINS=https://app.example.com` on the backend. To point it at a backend on another host or port, start it with `BACKEND_URL=http://host:port npm run dev`.

For a single-server setup, build the frontend once and FastAPI serves it at http://localhost:8001:

```bash
cd frontend && npm run build    # writes frontend/dist/, picked up on backend start
```

Milvus Lite allows only one process to open the database. While the backend is running, ingest through `POST /api/ingest`. Use `python -m backend.ingest` only when the backend is stopped.

## Voice

With `DEEPGRAM_API_KEY` set, the chat gets voice in both directions (Deepgram Nova-3 for speech-to-text, Aura-2 for text-to-speech):

- **Dictate a question:** click the mic next to Send and speak. Recording stops when you pause for 3 s, click stop or press Enter (Esc cancels), and the transcript is added to the end of the message box. Nothing is sent until you press Enter, so you can edit it, type more or dictate again. Or **hold Space** to talk and release it to stop (when the message box is empty or not focused); holding Space never stops at a pause. There's no time limit; if nothing is said for 15 s the mic turns off.
- **Spoken replies:** answers are read aloud while "Speak replies" is on (sidebar). Every answer also has a Listen button.
- **Hands-free conversation:** click the waveform button next to the mic and just talk. The bot answers when you finish speaking (Deepgram Flux detects the end of your turn) and you can interrupt it by speaking over it. "Stop" or "Wait" alone also stops it. Stop or Esc ends hands-free; it also stops by itself after 2 minutes of silence.

Browsers only allow the microphone on `https://` or `localhost`. Without a Deepgram key the mic is disabled and text chat works as usual.

## API

| Method | Path      | Body                                  | Returns                         |
|--------|-----------|---------------------------------------|---------------------------------|
| GET    | `/api/health` | –                                     | `{"status": "ok", "voice": true}` (`voice`: Deepgram key set) |
| POST   | `/api/chat`   | `{"question": "...", "history": [...]}` | `{"answer": "...", "sources": [...]}` |
| POST   | `/api/ingest` | –                                     | `{"files", "pages", "chunks"}`  |
| POST   | `/api/stt`    | multipart `audio` file (max 10 MB)    | `{"transcript": "..."}` (`""` if nothing was heard) |
| POST   | `/api/tts`    | `{"text": "..."}` (max 5000 chars)     | streamed `audio/mpeg`           |
| WS     | `/api/ws/voice` | hands-free voice                      | see `backend/voice_session.py` |

`/api/stt` and `/api/tts` return 503 when `DEEPGRAM_API_KEY` is not set; text chat works without it.

**Hands-free WebSocket.** The browser sends `{"type": "start", "history": [...]}`, then mic audio as binary PCM16 mono 16 kHz frames. Flux detects when the user has finished speaking, the RAG answers, and the server sends back JSON events (`user_turn`, `answer_sources`, `answer_delta`, `answer_done`, `status`, ...) plus the spoken reply as binary PCM16 mono 24 kHz. Pages from other origins are rejected (same-origin and `CORS_ORIGINS` only). To try it without a browser:

```bash
python scripts/voice_client.py question.wav   # PCM16 mono 16 kHz WAV; saves reply.wav
```

## Configuration (`.env`)

| Variable          | Default                                  |
|-------------------|------------------------------------------|
| `GROQ_API_KEY`    | – (required)                             |
| `GROQ_MODEL`      | `openai/gpt-oss-20b`                     |
| `ROUTER_MODEL`    | `openai/gpt-oss-20b`                     |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `MILVUS_DB_PATH`  | `./data/milvus.db`                       |
| `COLLECTION_NAME` | `docs_rag`                               |
| `DOCS_DIR`        | `./docs`                                 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` / `TOP_K` | `1000` / `150` / `4` |
| `CORS_ORIGINS`    | – (comma-separated origins, only if the frontend is hosted on another domain) |
| `DEEPGRAM_API_KEY` | – (optional; enables voice)            |
| `DEEPGRAM_STT_MODEL` / `DEEPGRAM_TTS_MODEL` | `nova-3` / `aura-2-thalia-en` |
| `DEEPGRAM_KEYTERMS` | – (comma-separated terms to boost in transcription) |
| `DEEPGRAM_FLUX_MODEL` / `DEEPGRAM_EOT_THRESHOLD` | `flux-general-en` / `0.7` (hands-free; higher waits longer before answering) |
| `VOICE_MAX_SESSIONS` | `5` (simultaneous hands-free sessions) |

Don't name a variable `MILVUS_URI`. pymilvus reads it from `.env` itself and expects a server URL.

If you change `EMBEDDING_MODEL`, re-run ingestion. Vectors from different models aren't compatible.

## Tests

```bash
uv pip install -r requirements-dev.txt --index-strategy unsafe-best-match
python -m pytest
```

The tests mock Deepgram, so they need no API key or network.
