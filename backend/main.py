import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.ingest import ingest
from backend.rag import RAGChain
from backend.voice import DeepgramClient, VoiceError, clean_for_speech

# Show our INFO logs (routing decisions, stt_ms / tts_ms timings) next to uvicorn's
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TTS_CHARS = 5000


def parse_origins(value: str) -> list[str]:
    return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]


def add_cors(app: FastAPI, origins: list[str]) -> None:
    """Allow the listed browser origins to call the API cross-domain. No-op when empty."""
    if not origins:
        return
    if "*" in origins:
        logger.warning("CORS_ORIGINS=* lets any website use this API (and its Deepgram credits)")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,  # browsers cache the preflight, so most calls skip the extra OPTIONS
    )
    logger.info("CORS enabled for %s", ", ".join(origins))


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


class STTResponse(BaseModel):
    transcript: str


class TTSRequest(BaseModel):
    text: str = Field(max_length=MAX_TTS_CHARS)


state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["rag"] = RAGChain()
    settings = get_settings()
    if settings.deepgram_api_key:
        state["voice"] = DeepgramClient(settings)
    else:
        logger.warning("DEEPGRAM_API_KEY is not set: voice endpoints are disabled")
    yield
    if "voice" in state:
        await state["voice"].aclose()
    state.clear()


def get_voice() -> DeepgramClient:
    voice = state.get("voice")
    if voice is None:
        raise HTTPException(status_code=503, detail="Voice is not configured (DEEPGRAM_API_KEY missing)")
    return voice


def voice_http_error(e: VoiceError) -> HTTPException:
    """Turn a Deepgram failure into a status the frontend can explain to the user."""
    logger.warning("Voice error: %s", e)
    if e.status in (401, 403):
        return HTTPException(status_code=502, detail="Voice service misconfigured (check DEEPGRAM_API_KEY)")
    if e.status == 429:
        return HTTPException(status_code=503, detail="Voice service is busy, try again in a moment")
    if e.timeout:
        return HTTPException(status_code=504, detail="Voice service timed out")
    return HTTPException(status_code=502, detail="Voice service error")


app = FastAPI(title="RAG Chatbot API", lifespan=lifespan)
add_cors(app, parse_origins(get_settings().cors_origins))
# All API routes live under /api, so they never clash with frontend files and the
# Vite dev proxy needs a single entry.
api = APIRouter(prefix="/api")


@api.get("/health")
def health():
    return {"status": "ok"}


@api.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty")
    try:
        return await run_in_threadpool(
            state["rag"].answer, req.question, [t.model_dump() for t in req.history]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.post("/stt", response_model=STTResponse)
async def stt(audio: UploadFile = File(...)):
    voice = get_voice()
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording is too long")
    if not data:
        raise HTTPException(status_code=400, detail="Recording is empty")
    try:
        return {"transcript": await voice.transcribe(data)}
    except VoiceError as e:
        if e.status == 400:  # Deepgram couldn't decode the audio
            raise HTTPException(status_code=400, detail="Couldn't read the recording (unsupported or corrupt audio)")
        raise voice_http_error(e)


@api.post("/tts")
async def tts(req: TTSRequest):
    voice = get_voice()
    if not clean_for_speech(req.text):
        raise HTTPException(status_code=400, detail="Nothing to speak")

    # Wait for the first chunk before answering, so a Deepgram error becomes a
    # proper error status rather than a stream that breaks after a 200.
    chunks = voice.stream_speech(req.text)
    try:
        first = await anext(chunks)
    except VoiceError as e:
        raise voice_http_error(e)
    except StopAsyncIteration:
        raise HTTPException(status_code=502, detail="Voice service returned no audio")

    async def body():
        yield first
        try:
            async for chunk in chunks:
                yield chunk
        except VoiceError:
            # Headers are already sent; the browser just gets shorter audio.
            logger.exception("TTS stream failed mid-way")

    return StreamingResponse(body(), media_type="audio/mpeg")


@api.post("/ingest")
async def ingest_docs():
    try:
        result = await run_in_threadpool(ingest)
        state["rag"].refresh()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.include_router(api)

# Serve the built React app (frontend/dist) when present. Mounted last so the
# API routes above take precedence over static files.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
