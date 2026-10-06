"""Voice endpoint tests with a fake Deepgram client (no network, no RAG startup)."""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.voice import VoiceError


class FakeVoice:
    def __init__(self, transcript="hello", chunks=(b"ID3", b"MP3"), error=None, fail_after_first=False):
        self.transcript = transcript
        self.chunks = chunks
        self.error = error
        self.fail_after_first = fail_after_first
        self.received = None
        self.warmed = 0

    async def warm_up(self):
        self.warmed += 1

    async def transcribe(self, audio: bytes) -> str:
        self.received = audio
        if self.error:
            raise self.error
        return self.transcript

    async def stream_speech(self, text: str):
        self.received = text
        if self.error:
            raise self.error
        for i, chunk in enumerate(self.chunks):
            if self.fail_after_first and i == 1:
                raise VoiceError("dropped", status=500)
            yield chunk


@pytest.fixture
def client():
    # Not used as a context manager, so the lifespan (RAG + Milvus) doesn't run.
    yield TestClient(main.app)
    main.state.pop("voice", None)


def use(voice):
    main.state["voice"] = voice
    return voice


# --- /stt -------------------------------------------------------------------


def test_stt_returns_transcript(client):
    voice = use(FakeVoice(transcript="What is habit stacking?"))
    res = client.post("/api/stt", files={"audio": ("q.webm", b"audio-bytes", "audio/webm")})
    assert res.status_code == 200
    assert res.json() == {"transcript": "What is habit stacking?"}
    assert voice.received == b"audio-bytes"


def test_stt_empty_upload_is_400(client):
    use(FakeVoice())
    res = client.post("/api/stt", files={"audio": ("q.webm", b"", "audio/webm")})
    assert res.status_code == 400


def test_stt_too_large_is_413(client, monkeypatch):
    use(FakeVoice())
    monkeypatch.setattr(main, "MAX_AUDIO_BYTES", 10)
    res = client.post("/api/stt", files={"audio": ("q.webm", b"x" * 11, "audio/webm")})
    assert res.status_code == 413


def test_stt_undecodable_audio_is_400(client):
    use(FakeVoice(error=VoiceError("corrupt or unsupported data", status=400)))
    res = client.post("/api/stt", files={"audio": ("q.webm", b"junk", "audio/webm")})
    assert res.status_code == 400
    assert "Couldn't read the recording" in res.json()["detail"]


def test_voice_disabled_without_key_is_503(client):
    main.state.pop("voice", None)
    assert client.post("/api/stt", files={"audio": ("q.webm", b"x", "audio/webm")}).status_code == 503
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == 503


@pytest.mark.parametrize(
    "error, status",
    [
        (VoiceError("bad key", status=401), 502),
        (VoiceError("busy", status=429), 503),
        (VoiceError("slow", timeout=True), 504),
        (VoiceError("boom", status=500), 502),
        (VoiceError("no network"), 502),
    ],
)
def test_voice_errors_map_to_status(client, error, status):
    use(FakeVoice(error=error))
    assert client.post("/api/stt", files={"audio": ("q.webm", b"x", "audio/webm")}).status_code == status
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == status


# --- /tts -------------------------------------------------------------------


def test_tts_streams_mp3(client):
    voice = use(FakeVoice(chunks=(b"ID3", b"-part2", b"-part3")))
    res = client.post("/api/tts", json={"text": "Hello there."})
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.content == b"ID3-part2-part3"
    assert voice.received == "Hello there."


@pytest.mark.parametrize("text", ["", "   ", "** **"])
def test_tts_nothing_to_speak_is_400(client, text):
    use(FakeVoice())
    assert client.post("/api/tts", json={"text": text}).status_code == 400


def test_tts_text_too_long_is_422(client):
    use(FakeVoice())
    res = client.post("/api/tts", json={"text": "a" * (main.MAX_TTS_CHARS + 1)})
    assert res.status_code == 422


def test_tts_no_audio_is_502(client):
    use(FakeVoice(chunks=()))
    assert client.post("/api/tts", json={"text": "Hi."}).status_code == 502


def test_tts_failure_mid_stream_returns_partial_audio(client):
    use(FakeVoice(chunks=(b"ID3", b"never"), fail_after_first=True))
    res = client.post("/api/tts", json={"text": "Hi."})
    assert res.status_code == 200
    assert res.content == b"ID3"


def test_routes_are_under_api_prefix(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/health").status_code == 404


def test_warmup_calls_voice_and_is_noop_without_key(client):
    voice = use(FakeVoice())
    assert client.post("/api/voice/warmup").status_code == 204
    assert voice.warmed == 1
    main.state.pop("voice")
    assert client.post("/api/voice/warmup").status_code == 204


def test_health_reports_voice_availability(client):
    main.state.pop("voice", None)
    assert client.get("/api/health").json() == {"status": "ok", "voice": False}
    use(FakeVoice())
    assert client.get("/api/health").json() == {"status": "ok", "voice": True}
