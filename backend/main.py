import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.ingest import ingest
from backend.rag import RAGChain
from backend.voice import DeepgramClient, VoiceError, clean_for_speech
from backend.voice_session import ProtocolError, VoiceSession

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


def origin_allowed(origin: str | None, host: str | None, origins: list[str]) -> bool:
    """WebSocket origin check. Browsers don't apply CORS to WebSockets, so any
    website could otherwise open /api/ws/voice from a visitor's browser.

    Allows same-origin pages and CORS_ORIGINS. Clients without an Origin header
    are not browsers (e.g. scripts/voice_client.py); an Origin check can't stop
    those anyway.
    """
    if origin is None or "*" in origins:
        return True
    origin = origin.rstrip("/")
    return origin in origins or urlsplit(origin).netloc == host


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


def describe_voice_error(e: VoiceError) -> tuple[int, str]:
    """Turn a Deepgram failure into an HTTP status and a message the frontend can show."""
    logger.warning("Voice error: %s", e)
    if e.status in (401, 403):
        return 502, "Voice service misconfigured (check DEEPGRAM_API_KEY)"
    if e.status == 429:
        return 503, "Voice service is busy, try again in a moment"
    if e.timeout:
        return 504, "Voice service timed out"
    return 502, "Voice service error"


def voice_http_error(e: VoiceError) -> HTTPException:
    status, detail = describe_voice_error(e)
    return HTTPException(status_code=status, detail=detail)


app = FastAPI(title="RAG Chatbot API", lifespan=lifespan)
CORS_ORIGINS = parse_origins(get_settings().cors_origins)
add_cors(app, CORS_ORIGINS)
# Hands-free sessions stream paid STT for as long as they're open
voice_sessions = asyncio.Semaphore(get_settings().voice_max_sessions)
# All API routes live under /api, so they never clash with frontend files and the
# Vite dev proxy needs a single entry.
api = APIRouter(prefix="/api")


@api.get("/health")
def health():
    # `voice` tells the frontend whether to enable the mic and spoken replies
    return {"status": "ok", "voice": "voice" in state}


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


@api.post("/voice/warmup", status_code=204)
async def voice_warmup():
    """Called by the frontend when it sends a question, so the reply's TTS call
    doesn't pay for a fresh Deepgram connection. No-op when voice is disabled."""
    if "voice" in state:
        await state["voice"].warm_up()


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


async def answer_async(question: str, history: list[dict]) -> dict:
    return await run_in_threadpool(state["rag"].answer, question, history)


@api.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    """Hands-free voice: protocol in backend/voice_session.py."""
    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host"), CORS_ORIGINS):
        logger.warning("Rejected voice WebSocket from origin %s", ws.headers.get("origin"))
        await ws.close(code=1008)  # before accept(): the browser sees a failed handshake (403)
        return
    await ws.accept()

    async def fail(message: str, code: int) -> None:
        try:
            await ws.send_json({"type": "error", "message": message})
            await ws.close(code=code)
        except (WebSocketDisconnect, RuntimeError):  # the browser already left
            pass

    voice = state.get("voice")
    if voice is None:
        return await fail("Voice is not configured (DEEPGRAM_API_KEY missing)", 1011)
    if voice_sessions.locked():
        return await fail("Too many voice sessions are open. Try again later.", 1013)

    async with voice_sessions:
        try:
            await VoiceSession(ws, voice, answer_async).run()
        except WebSocketDisconnect:
            return
        except ProtocolError as e:
            return await fail(str(e), 1008)
        except VoiceError as e:
            return await fail(describe_voice_error(e)[1], 1011)
        except Exception:
            logger.exception("Voice session failed")
            return await fail("Voice session error", 1011)
    await ws.close()


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
