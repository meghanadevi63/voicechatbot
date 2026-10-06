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

- **Ask by voice:** click the mic next to Send, speak, then click send (or press Enter). Esc cancels. Recordings stop automatically after 60 s.
- **Spoken replies:** answers are read aloud while "Speak replies" is on (sidebar). Every answer also has a Listen button.

Browsers only allow the microphone on `https://` or `localhost`. Without a Deepgram key the mic is disabled and text chat works as usual.

## API

| Method | Path      | Body                                  | Returns                         |
|--------|-----------|---------------------------------------|---------------------------------|
| GET    | `/api/health` | –                                     | `{"status": "ok", "voice": true}` (`voice`: Deepgram key set) |
| POST   | `/api/chat`   | `{"question": "...", "history": [...]}` | `{"answer": "...", "sources": [...]}` |
| POST   | `/api/ingest` | –                                     | `{"files", "pages", "chunks"}`  |
| POST   | `/api/stt`    | multipart `audio` file (max 10 MB)    | `{"transcript": "..."}` (`""` if nothing was heard) |
| POST   | `/api/tts`    | `{"text": "..."}` (max 5000 chars)     | streamed `audio/mpeg`           |

`/api/stt` and `/api/tts` return 503 when `DEEPGRAM_API_KEY` is not set; text chat works without it.

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

Don't name a variable `MILVUS_URI`. pymilvus reads it from `.env` itself and expects a server URL.

If you change `EMBEDDING_MODEL`, re-run ingestion. Vectors from different models aren't compatible.

## Tests

```bash
uv pip install -r requirements-dev.txt --index-strategy unsafe-best-match
python -m pytest
```

The tests mock Deepgram, so they need no API key or network.
