"""Hands-free voice session: one browser WebSocket <-> Flux (STT) -> RAG -> Aura-2 (TTS).

Protocol (see plan_voice.md, "WebSocket protocol"):

Browser -> server
  JSON {"type": "start", "history": [...]}   first message; seeds the conversation
  binary                                     mic audio, PCM16 mono 16 kHz, ~80 ms per frame
  JSON {"type": "playback_done"}             the browser finished playing the reply audio
  JSON {"type": "stop"}                      ends the session

Server -> browser
  binary                                     reply audio, PCM16 mono 24 kHz
  JSON {"type": "status", "status": "listening" | "thinking" | "speaking"}
  JSON {"type": "partial_transcript", "text": ...}
  JSON {"type": "user_turn", "text": ...}
  JSON {"type": "answer_sources", "sources": [...]}
  JSON {"type": "answer_delta", "text": ...}
  JSON {"type": "answer_done", "interrupted": bool, "error"?: ...}
  JSON {"type": "stop_playback"}             barge-in: drop all queued reply audio now
  JSON {"type": "error", "message": ...}     the session ends after this

Reply audio arrives several times faster than it plays, so the bot counts as
speaking until the browser reports playback_done (then status -> listening).

Barge-in: if the user says at least BARGE_IN_MIN_WORDS while the bot is
thinking or speaking, the answer is cancelled, Aura-2 is cleared and the
browser gets stop_playback. Shorter sounds ("mm", or the bot's own voice leaking
past echo cancellation as a single word) don't interrupt, except stop words
("Stop.", "Wait"): those interrupt on their own and aren't answered.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from backend.voice import DeepgramClient, LiveListener, LiveSpeaker, VoiceError, clean_for_speech

logger = logging.getLogger(__name__)

# No reply audio for this long while speaking: give up on the speech, keep the text.
# Aura-2 normally sends audio with gaps under 0.1 s, but occasionally stalls for seconds.
STALL_TIMEOUT_S = 3.0
# The RAG uses only the last few turns; this just bounds memory for long sessions.
MAX_HISTORY = 20
# Echo guard: words the user must say before they interrupt the bot.
BARGE_IN_MIN_WORDS = 2
# ...except these, which interrupt on their own. A turn made only of them
# ("Stop.", "OK wait") just stops the bot; it isn't sent to the RAG as a question.
STOP_WORDS = {"stop", "wait", "pause", "quiet", "enough", "cancel", "shush"}
FILLER_WORDS = {"ok", "okay", "please", "now", "just", "hey", "no", "hold", "on"}


def is_stop_command(transcript: str) -> bool:
    words = set(re.findall(r"[a-z']+", transcript.lower()))
    return bool(words & STOP_WORDS) and words <= STOP_WORDS | FILLER_WORDS

AnswerFn = Callable[[str, list[dict]], Awaitable[dict]]


class ProtocolError(Exception):
    """The browser sent something the protocol doesn't allow."""


def parse_history(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [
        {"role": t["role"], "content": t["content"]}
        for t in raw
        if isinstance(t, dict) and t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)
    ][-MAX_HISTORY:]


class VoiceSession:
    def __init__(self, ws: WebSocket, voice: DeepgramClient, answer: AnswerFn):
        self.ws = ws
        self.voice = voice
        self.answer = answer
        self.history: list[dict] = []
        self.stt: LiveListener | None = None
        self.tts: LiveSpeaker | None = None
        self.answer_task: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        # Speech of the current answer
        self.flushed: asyncio.Event | None = None
        self.last_audio = 0.0
        self.first_audio_at: float | None = None
        self.dropping = False  # after clear(): ignore audio until Aura-2 confirms "Cleared"
        self.playing = False  # the browser is playing reply audio (until playback_done)

    # --- sending to the browser ------------------------------------------------

    async def send_event(self, type_: str, **data) -> None:
        async with self.send_lock:
            await self.ws.send_text(json.dumps({"type": type_, **data}))

    async def send_audio(self, chunk: bytes) -> None:
        async with self.send_lock:
            await self.ws.send_bytes(chunk)

    # --- session ---------------------------------------------------------------

    async def run(self) -> None:
        """Run until the browser sends stop or disconnects. Raises VoiceError if Deepgram fails."""
        try:
            start = json.loads(await self.ws.receive_text())
        except (json.JSONDecodeError, KeyError):  # KeyError: a binary frame came first
            start = None
        if not isinstance(start, dict) or start.get("type") != "start":
            raise ProtocolError('The first message must be {"type": "start"}')
        self.history = parse_history(start.get("history"))

        async with self.voice.live_listener() as stt, self.voice.live_speaker() as tts:
            self.stt, self.tts = stt, tts
            await self.send_event("status", status="listening")
            tasks = [
                asyncio.create_task(self.mic_pump(), name="mic_pump"),
                asyncio.create_task(self.stt_events(), name="stt_events"),
                asyncio.create_task(self.tts_pump(), name="tts_pump"),
            ]
            try:
                # mic_pump returns when the browser stops; the others only end on errors.
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()  # re-raise a Deepgram error
            finally:
                for task in [*tasks, self.answer_task]:
                    if task and not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, *([self.answer_task] if self.answer_task else []), return_exceptions=True)

    async def mic_pump(self) -> None:
        """Browser mic audio -> Flux. Returns on stop; raises WebSocketDisconnect if the browser leaves."""
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(msg.get("code", 1000))
            if msg.get("bytes"):
                await self.stt.send(msg["bytes"])
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    raise ProtocolError("Messages must be JSON or binary audio") from None
                kind = data.get("type") if isinstance(data, dict) else None
                if kind == "stop":
                    return
                if kind == "playback_done" and self.playing:
                    self.playing = False
                    await self.send_event("status", status="listening")

    def bot_active(self) -> bool:
        """Thinking, sending speech, or the browser is still playing it."""
        return self.playing or bool(self.answer_task and not self.answer_task.done())

    async def stt_events(self) -> None:
        """Flux turn events -> live transcript in the UI; a finished turn starts an answer."""
        async for turn in self.stt.events():
            command = is_stop_command(turn.transcript)
            enough_words = command or len(turn.transcript.split()) >= BARGE_IN_MIN_WORDS
            if turn.event in ("StartOfTurn", "Update", "TurnResumed") and turn.transcript:
                await self.send_event("partial_transcript", text=turn.transcript)
                if self.bot_active() and enough_words:
                    await self.barge_in(turn.transcript)
            elif turn.event == "EndOfTurn" and turn.transcript:
                if command:
                    if self.bot_active():
                        await self.barge_in(turn.transcript)
                    else:  # nothing to stop; clear the live transcript
                        await self.send_event("status", status="listening")
                    logger.info("Stop command, not answered: %r", turn.transcript)
                    continue
                if self.bot_active():
                    if not enough_words:
                        logger.info("Ignored short turn while the bot is answering: %r", turn.transcript)
                        continue
                    await self.barge_in(turn.transcript)
                self.answer_task = asyncio.create_task(self.respond(turn.transcript, time.perf_counter()))

    async def barge_in(self, heard: str) -> None:
        """The user started talking over the bot: stop answering and speaking now."""
        logger.info("Barge-in: %r", heard)
        task = self.answer_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.send_event("answer_done", interrupted=True)
        self.playing = False
        self.dropping = True
        await self.tts.clear()
        await self.send_event("stop_playback")
        await self.send_event("status", status="listening")

    async def tts_pump(self) -> None:
        """Aura-2 audio -> browser."""
        async for item in self.tts.events():
            if isinstance(item, bytes):
                if self.dropping:
                    continue
                self.last_audio = time.perf_counter()
                if self.first_audio_at is None:
                    self.first_audio_at = self.last_audio
                    self.playing = True
                    await self.send_event("status", status="speaking")
                await self.send_audio(item)
            elif item == "Flushed" and self.flushed:
                self.flushed.set()
            elif item == "Cleared":
                self.dropping = False

    # --- one turn --------------------------------------------------------------

    async def respond(self, question: str, turn_end: float) -> None:
        """Answer one user turn: text to the UI first, then speech."""
        self.first_audio_at = None
        await self.send_event("user_turn", text=question)
        await self.send_event("status", status="thinking")
        try:
            result = await self.answer(question, list(self.history))
        except Exception:
            logger.exception("Answer failed for %r", question)
            await self.send_event("answer_done", interrupted=False, error="Couldn't answer that. Please try again.")
            await self.send_event("status", status="listening")
            return
        answer_at = time.perf_counter()

        answer = result["answer"]
        await self.send_event("answer_sources", sources=result.get("sources", []))
        await self.send_event("answer_delta", text=answer)
        self.history = [*self.history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}][
            -MAX_HISTORY:
        ]

        try:
            speech_error = await self.speak(answer) if clean_for_speech(answer) else None
        except VoiceError as e:
            logger.warning("Speaking the answer failed: %s", e)
            speech_error = "Couldn't speak the answer. It's shown as text."
        logger.info(
            "voice_turn eot_to_answer_ms=%d eot_to_first_audio_ms=%s",
            (answer_at - turn_end) * 1000,
            f"{(self.first_audio_at - turn_end) * 1000:.0f}" if self.first_audio_at else "-",
        )
        done = {"error": speech_error} if speech_error else {}
        await self.send_event("answer_done", interrupted=False, **done)
        if not self.playing:  # otherwise "listening" follows the browser's playback_done
            await self.send_event("status", status="listening")

    async def speak(self, text: str) -> str | None:
        """Send the answer to Aura-2 and wait until all its audio has gone out.

        Returns an error message if the speech stalled, None when it finished.
        """
        self.flushed = asyncio.Event()
        self.dropping = False
        self.last_audio = time.perf_counter()
        await self.tts.speak(text)
        await self.tts.flush()
        while not self.flushed.is_set():
            try:
                await asyncio.wait_for(self.flushed.wait(), timeout=0.25)
            except TimeoutError:
                if time.perf_counter() - self.last_audio > STALL_TIMEOUT_S:
                    logger.warning("Speech stalled for %.0f s; giving up on it", STALL_TIMEOUT_S)
                    self.dropping = True
                    await self.tts.clear()
                    return "The voice stopped responding. The answer is shown as text."
        return None
