"""Async Deepgram client: speech-to-text (Nova-3) and text-to-speech (Aura-2).

Wraps the official SDK (pinned in requirements.txt; its API changes between
major versions, so all SDK calls stay in this module).
"""

import logging
import re
import time
from collections.abc import AsyncIterator

import httpx
from deepgram import AsyncDeepgramClient
from deepgram.core.api_error import ApiError

from backend.config import Settings

logger = logging.getLogger(__name__)

TIMEOUT_S = 15.0
# One retry on 429/5xx: more would push spoken replies past the latency budget.
MAX_RETRIES = 1
# Aura-2 rejects requests over 2000 characters; stay safely under it.
TTS_MAX_CHARS = 1900


class VoiceError(Exception):
    """A Deepgram call failed. `status` is Deepgram's HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None, timeout: bool = False):
        super().__init__(message)
        self.status = status
        self.timeout = timeout


def clean_for_speech(text: str) -> str:
    """Strip markdown and URLs the LLM may still emit, so TTS doesn't read symbols aloud."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"https?://\S+?(?=[.,!?;:]?(?:\s|$))", "", text)  # keep trailing punctuation
    text = re.sub(r"`+", "", text)
    text = re.sub(r"(\*\*|__|\*)(\S.*?\S|\S)\1", r"\2", text)  # bold / italic
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", text, flags=re.MULTILINE)  # list markers
    text = re.sub(r"\*{2,}|_{2,}", "", text)  # unpaired bold markers
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" ([.,!?;:])", r"\1", text).strip()  # "or ." left by a removed URL
    # Only punctuation/symbols left: nothing worth speaking
    return text if re.search(r"\w", text) else ""


def split_for_tts(text: str, limit: int = TTS_MAX_CHARS) -> list[str]:
    """Split text into pieces of at most `limit` chars, at sentence boundaries where possible."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        # A single sentence longer than the limit: hard-split it at word boundaries.
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return pieces


def _voice_error(e: Exception) -> VoiceError:
    """Map SDK / transport exceptions to VoiceError."""
    if isinstance(e, ApiError):
        return VoiceError(f"Deepgram error {e.status_code}: {str(e.body)[:200]}", status=e.status_code)
    if isinstance(e, httpx.TimeoutException):
        return VoiceError("Deepgram request timed out", timeout=True)
    return VoiceError(f"Can't reach Deepgram: {e}")


class DeepgramClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.stt_model = settings.deepgram_stt_model
        self.tts_model = settings.deepgram_tts_model
        self.keyterms = [t.strip() for t in settings.deepgram_keyterms.split(",") if t.strip()]
        # `http` lets tests inject a mock transport
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT_S)
        self.dg = AsyncDeepgramClient(
            api_key=settings.deepgram_api_key,
            timeout=TIMEOUT_S,
            max_retries=MAX_RETRIES,
            httpx_client=self.http,
        )

    async def aclose(self):
        await self.http.aclose()

    async def warm_up(self) -> None:
        """Re-open the connection to Deepgram ahead of a TTS/STT call.

        Deepgram drops idle connections after a few seconds, and reconnecting adds
        ~0.5 s to the next call (measured: TTS first audio 0.95 s cold vs 0.39 s
        warm). Call this while the RAG is still answering. Listing projects is free;
        any response, even a 403 for a restricted key, leaves the connection open.
        """
        try:
            await self.dg.manage.v1.projects.list(request_options={"max_retries": 0})
        except Exception as e:  # best effort: never fail a request over a warm-up
            logger.debug("Deepgram warm-up failed: %s", e)

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe a recorded clip. Returns "" when nothing intelligible was said.

        Deepgram detects the container (WebM/Opus, MP4/AAC, WAV...) from the bytes.
        """
        start = time.perf_counter()
        try:
            res = await self.dg.listen.v1.media.transcribe_file(
                request=audio,
                model=self.stt_model,
                smart_format=True,
                language="en",
                keyterm=self.keyterms or None,
            )
        except (ApiError, httpx.HTTPError) as e:
            raise _voice_error(e) from e
        logger.info("stt_ms=%d", (time.perf_counter() - start) * 1000)
        try:
            return (res.results.channels[0].alternatives[0].transcript or "").strip()
        except (AttributeError, IndexError):
            return ""

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        """Yield MP3 chunks as Deepgram generates them.

        Aura-2 sends the first audio after ~0.4 s but needs several seconds for a
        full reply, so callers should play chunks as they arrive. Long text is
        split into pieces; their MP3 frames simply follow each other.
        """
        pieces = split_for_tts(clean_for_speech(text))
        if not pieces:
            raise VoiceError("Nothing to speak")
        start = time.perf_counter()
        first_ms = None
        try:
            for piece in pieces:
                async for chunk in self.dg.speak.v1.audio.generate(
                    text=piece, model=self.tts_model, encoding="mp3"
                ):
                    if first_ms is None:
                        first_ms = (time.perf_counter() - start) * 1000
                    yield chunk
        except (ApiError, httpx.HTTPError) as e:
            raise _voice_error(e) from e
        logger.info(
            "tts_first_audio_ms=%d tts_ms=%d pieces=%d",
            first_ms or 0,
            (time.perf_counter() - start) * 1000,
            len(pieces),
        )

    async def synthesize(self, text: str) -> bytes:
        """Synthesize the whole reply as one MP3."""
        return b"".join([chunk async for chunk in self.stream_speech(text)])
