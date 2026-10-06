# RAG Chatbot (Streamlit + FastAPI + LangChain + Milvus Lite)

Ask questions about the PDFs in `docs/`. Answers come from Groq, grounded in chunks retrieved from a local Milvus Lite database (`data/milvus.db` — no Docker or server needed).

```
Streamlit (frontend/app.py) ──HTTP──▶ FastAPI (backend/main.py) ──▶ RAG chain (backend/rag.py) ──▶ Groq LLM
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

cp .env.example .env    # then set GROQ_API_KEY
```

## Run

```bash
# 1. Backend (port 8001)
uvicorn backend.main:app --port 8001

# 2. Index the docs (first run, or after adding PDFs) — or use "Re-ingest docs" in the UI sidebar
curl -X POST localhost:8001/ingest

# 3. Frontend
streamlit run frontend/app.py
```

Milvus Lite allows only one process to open the database. While the backend is running, ingest through `POST /ingest`. Use `python -m backend.ingest` only when the backend is stopped.

## API

| Method | Path      | Body                                  | Returns                         |
|--------|-----------|---------------------------------------|---------------------------------|
| GET    | `/health` | –                                     | `{"status": "ok"}`              |
| POST   | `/chat`   | `{"question": "...", "history": [...]}` | `{"answer": "...", "sources": [...]}` |
| POST   | `/ingest` | –                                     | `{"files", "pages", "chunks"}`  |

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
| `BACKEND_URL`     | `http://localhost:8001`                  |

Don't name a variable `MILVUS_URI`. pymilvus reads it from `.env` itself and expects a server URL.

If you change `EMBEDDING_MODEL`, re-run ingestion. Vectors from different models aren't compatible.
