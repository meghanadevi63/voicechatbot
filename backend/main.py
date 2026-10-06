from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from backend.ingest import ingest
from backend.rag import RAGChain


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str
    history: list[ChatTurn] = []


class Source(BaseModel):
    source: str | None
    page: int | None
    content: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]


state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["rag"] = RAGChain()
    yield
    state.clear()


app = FastAPI(title="RAG Chatbot API", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty")
    try:
        return await run_in_threadpool(
            state["rag"].answer, req.question, [t.model_dump() for t in req.history]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest")
async def ingest_docs():
    try:
        result = await run_in_threadpool(ingest)
        state["rag"].refresh()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
