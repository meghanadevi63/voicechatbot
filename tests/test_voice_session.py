"""Hands-free WebSocket tests with fake Flux / Aura-2 sockets and a fake RAG (no network)."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import main, voice_session
from backend.voice import TurnEvent, VoiceError


class FakeListener:
    """Flux stand-in: an audio frame b"say:<text>" becomes one spoken turn."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.audio = []

    async def send(self, audio: bytes):
        self.audio.append(audio)
        if audio.startswith(b"say:"):
            text = audio[4:].decode()
            for event, transcript in [("StartOfTurn", ""), ("Update", text), ("EndOfTurn", text)]:
                await self.queue.put(TurnEvent(event, transcript))
        elif audio == b"silence-turn":
            await self.queue.put(TurnEvent("EndOfTurn", ""))

    async def events(self):
        while True:
            yield await self.queue.get()


class FakeSpeaker:
    """Aura-2 stand-in: flush() sends two audio chunks then "Flushed", unless stalled."""

    def __init__(self, stall=False):
        self.queue = asyncio.Queue()
        self.spoken = []
        self.stall = stall
        self.cleared = 0

    async def speak(self, text):
        self.spoken.append(text)

    async def flush(self):
        if self.stall:
            return
        for item in (b"pcm-1", b"pcm-2", "Flushed"):
            await self.queue.put(item)

    async def clear(self):
        self.cleared += 1
        await self.queue.put("Cleared")

    async def events(self):
        while True:
            yield await self.queue.get()


class FakeVoice:
    def __init__(self, stall=False, connect_error=None):
        self.listener = FakeListener()
        self.speaker = FakeSpeaker(stall=stall)
        self.connect_error = connect_error

    @asynccontextmanager
    async def live_listener(self):
        if self.connect_error:
            raise self.connect_error
        yield self.listener

    @asynccontextmanager
    async def live_speaker(self):
        yield self.speaker


class FakeRAG:
    def __init__(self):
        self.calls = []

    def answer(self, question, history):
        self.calls.append((question, list(history)))
        return {"answer": f"Answer to {question}", "sources": [{"source": "doc.pdf", "page": 1, "content": "..."}]}


@pytest.fixture
def setup(monkeypatch):
    voice, rag = FakeVoice(), FakeRAG()
    main.state["voice"] = voice
    main.state["rag"] = rag
    monkeypatch.setattr(main, "voice_sessions", asyncio.Semaphore(5))
    yield TestClient(main.app), voice, rag
    main.state.clear()


def receive_until(ws, type_, status=None):
    """Collect messages up to and including the first `type_` (and `status`, for status events)."""
    seen = []
    while True:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            seen.append(msg["bytes"])
            continue
        data = json.loads(msg["text"])
        seen.append(data)
        if data["type"] == type_ and (status is None or data.get("status") == status):
            return seen


def stop(ws):
    """End the session like the browser does, and wait for the server to close it.

    Leaving `websocket_connect` while the session is still running makes the
    TestClient cancel the app mid-flight, which uvicorn never does.
    """
    ws.send_json({"type": "stop"})
    while ws.receive()["type"] != "websocket.close":
        pass


def finish_reply(ws):
    """Receive one reply up to answer_done, then report playback finished like the browser."""
    messages = receive_until(ws, "answer_done")
    ws.send_json({"type": "playback_done"})
    return messages + receive_until(ws, "status", "listening")


def types(messages):
    return [m if isinstance(m, bytes) else (m["type"], m.get("status")) if m["type"] == "status" else m["type"] for m in messages]


def test_full_turn(setup):
    client, voice, rag = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start", "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}]})
        assert ws.receive_json() == {"type": "status", "status": "listening"}

        ws.send_bytes(b"say:What is habit stacking?")
        messages = finish_reply(ws)
        stop(ws)

    assert types(messages) == [
        "partial_transcript",
        "user_turn",
        ("status", "thinking"),
        "answer_sources",
        "answer_delta",
        ("status", "speaking"),
        b"pcm-1",
        b"pcm-2",
        "answer_done",
        ("status", "listening"),
    ]
    assert messages[1]["text"] == "What is habit stacking?"
    assert messages[4]["text"] == "Answer to What is habit stacking?"
    assert messages[3]["sources"][0]["source"] == "doc.pdf"
    assert messages[8] == {"type": "answer_done", "interrupted": False}
    # History from "start" reaches the RAG; the reply was spoken
    assert rag.calls == [("What is habit stacking?", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}])]
    assert voice.speaker.spoken == ["Answer to What is habit stacking?"]


def test_follow_up_turn_gets_previous_turn_as_history(setup):
    client, _, rag = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start", "history": []})
        ws.receive_json()
        ws.send_bytes(b"say:first question")
        finish_reply(ws)
        ws.send_bytes(b"say:second question")
        finish_reply(ws)
        stop(ws)
    assert rag.calls[1] == (
        "second question",
        [{"role": "user", "content": "first question"}, {"role": "assistant", "content": "Answer to first question"}],
    )


def test_barge_in_while_speaking_stops_playback_and_answers_the_new_turn(setup):
    client, voice, rag = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"say:first question")
        receive_until(ws, "answer_done")  # all audio sent; the browser is still playing it
        ws.send_bytes(b"say:wait, what about habits?")
        messages = receive_until(ws, "user_turn")
        finish_reply(ws)
        stop(ws)
    assert types(messages) == ["partial_transcript", "stop_playback", ("status", "listening"), "user_turn"]
    assert voice.speaker.cleared == 1
    assert [q for q, _ in rag.calls] == ["first question", "wait, what about habits?"]


def test_barge_in_while_thinking_cancels_the_answer(setup):
    client, voice, rag = setup
    slow = rag.answer
    rag.answer = lambda q, h: (__import__("time").sleep(0.5), slow(q, h))[1]
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"say:first question")
        receive_until(ws, "status", "thinking")
        ws.send_bytes(b"say:never mind that")
        messages = receive_until(ws, "user_turn")
        rest = finish_reply(ws)
        stop(ws)
    assert types(messages) == [
        "partial_transcript",
        "answer_done",
        "stop_playback",
        ("status", "listening"),
        "user_turn",
    ]
    assert messages[1] == {"type": "answer_done", "interrupted": True}
    # Only the new question was answered and spoken
    assert [m["text"] for m in rest if isinstance(m, dict) and m["type"] == "answer_delta"] == ["Answer to never mind that"]
    assert voice.speaker.spoken == ["Answer to never mind that"]


def test_one_word_while_speaking_does_not_interrupt(setup):
    client, voice, rag = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"say:first question")
        receive_until(ws, "answer_done")
        ws.send_bytes(b"say:mm")
        ws.send_json({"type": "playback_done"})
        messages = receive_until(ws, "status", "listening")
        stop(ws)
    assert types(messages) == ["partial_transcript", ("status", "listening")]
    assert voice.speaker.cleared == 0
    assert len(rag.calls) == 1


def test_stop_word_interrupts_and_is_not_answered(setup):
    client, voice, rag = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"say:first question")
        receive_until(ws, "answer_done")
        ws.send_bytes(b"say:Stop.")
        messages = receive_until(ws, "status", "listening")  # interrupted on the partial transcript
        messages += receive_until(ws, "status", "listening")  # its end of turn: not answered
        ws.send_bytes(b"say:Okay, wait.")  # nothing to stop now: still not a question
        messages += receive_until(ws, "status", "listening")
        stop(ws)
    assert types(messages) == [
        "partial_transcript",
        "stop_playback",
        ("status", "listening"),
        ("status", "listening"),
        "partial_transcript",
        ("status", "listening"),
    ]
    assert voice.speaker.cleared == 1
    assert [q for q, _ in rag.calls] == ["first question"]


@pytest.mark.parametrize(
    "text, command",
    [("Stop.", True), ("OK, wait", True), ("hold on, stop please", True), ("Stop smoking tips?", False), ("ok", False)],
)
def test_is_stop_command(text, command):
    assert voice_session.is_stop_command(text) is command


def test_audio_reaches_flux_and_empty_turns_are_ignored(setup):
    client, voice, rag = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"\x00\x01" * 1280)
        ws.send_bytes(b"silence-turn")
        ws.send_bytes(b"say:hello")
        messages = receive_until(ws, "user_turn")
        stop(ws)
    assert messages[-1]["text"] == "hello"
    assert voice.listener.audio[0] == b"\x00\x01" * 1280
    assert rag.calls[0][0] == "hello"


def test_stop_closes_the_session(setup):
    client, _, _ = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 1000


def test_first_message_must_be_start(setup):
    client, _, _ = setup
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_bytes(b"audio before start")
        assert ws.receive_json()["type"] == "error"


def test_stalled_speech_keeps_the_text_and_clears_tts(setup, monkeypatch):
    client, voice, _ = setup
    voice.speaker.stall = True
    monkeypatch.setattr(voice_session, "STALL_TIMEOUT_S", 0.3)
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"say:hello")
        messages = receive_until(ws, "answer_done")
        stop(ws)
    assert messages[-2]["type"] == "answer_delta"  # the text still arrived
    assert "voice stopped responding" in messages[-1]["error"]
    assert voice.speaker.cleared == 1


def test_rag_failure_reports_and_keeps_listening(setup):
    client, _, rag = setup
    rag.answer = lambda q, h: 1 / 0
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(b"say:hello")
        messages = receive_until(ws, "status", "listening")
        stop(ws)
    assert messages[-2]["type"] == "answer_done" and "answer that" in messages[-2]["error"]


def test_deepgram_connect_error_is_reported(setup):
    client, voice, _ = setup
    voice.connect_error = VoiceError("bad key", status=401)
    with client.websocket_connect("/api/ws/voice") as ws:
        ws.send_json({"type": "start"})
        msg = ws.receive_json()
    assert msg == {"type": "error", "message": "Voice service misconfigured (check DEEPGRAM_API_KEY)"}


def test_voice_disabled_is_reported(setup):
    client, _, _ = setup
    main.state.pop("voice")
    with client.websocket_connect("/api/ws/voice") as ws:
        assert "not configured" in ws.receive_json()["message"]


def test_session_limit(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(main, "voice_sessions", asyncio.Semaphore(0))
    with client.websocket_connect("/api/ws/voice") as ws:
        assert "Too many voice sessions" in ws.receive_json()["message"]


def test_other_origin_is_rejected(setup):
    client, _, _ = setup
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/voice", headers={"Origin": "https://evil.example"}):
            pass


@pytest.mark.parametrize(
    "origin, host, allowed, ok",
    [
        (None, "localhost:8001", [], True),  # not a browser
        ("http://localhost:5173", "localhost:5173", [], True),  # same origin (via the Vite proxy)
        ("http://localhost:5173/", "localhost:8001", ["http://localhost:5173"], True),  # CORS_ORIGINS
        ("https://evil.example", "localhost:8001", ["http://localhost:5173"], False),
        ("https://evil.example", "localhost:8001", ["*"], True),
    ],
)
def test_origin_allowed(origin, host, allowed, ok):
    assert main.origin_allowed(origin, host, allowed) is ok
