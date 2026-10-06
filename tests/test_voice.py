import httpx
import pytest

import backend.voice
from backend.config import Settings
from backend.voice import (
    TTS_MAX_CHARS,
    DeepgramClient,
    VoiceError,
    clean_for_speech,
    split_for_tts,
)


@pytest.fixture(autouse=True)
def no_retries(monkeypatch):
    # The SDK retries 429/5xx with backoff; keep tests fast and call counts exact.
    monkeypatch.setattr(backend.voice, "MAX_RETRIES", 0)


def make_client(handler, **overrides) -> DeepgramClient:
    settings = Settings(_env_file=None, deepgram_api_key="test-key", **overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DeepgramClient(settings, http=http)


def stt_body(transcript: str) -> dict:
    return {"results": {"channels": [{"alternatives": [{"transcript": transcript}]}]}}


# --- clean_for_speech -------------------------------------------------------


def test_clean_strips_markdown_and_urls():
    text = "## Summary\n**Habits** are *small*. See [the book](http://x.com) or https://y.com.\n- one\n2. two"
    assert clean_for_speech(text) == "Summary Habits are small. See the book or. one two"


def test_clean_keeps_plain_text_and_snake_case():
    assert clean_for_speech("Use my_var, 3 * 4 is twelve.") == "Use my_var, 3 * 4 is twelve."


# --- split_for_tts ----------------------------------------------------------


def test_split_short_text_is_one_piece():
    assert split_for_tts("Hello there. How are you?") == ["Hello there. How are you?"]


def test_split_groups_sentences_under_limit():
    text = "Aaaa. Bbbb. Cccc."
    assert split_for_tts(text, limit=11) == ["Aaaa. Bbbb.", "Cccc."]


def test_split_long_text_respects_limit_and_keeps_all_words():
    sentence = "This is a sentence about building better habits every single day. "
    text = sentence * 80  # ~5400 chars
    pieces = split_for_tts(text)
    assert len(pieces) > 1
    assert all(len(p) <= TTS_MAX_CHARS for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_split_hard_splits_a_sentence_without_punctuation():
    text = "word " * 1000  # 5000 chars, no sentence end
    pieces = split_for_tts(text)
    assert all(len(p) <= TTS_MAX_CHARS for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_split_empty():
    assert split_for_tts("   ") == []


# --- transcribe -------------------------------------------------------------


@pytest.mark.anyio
async def test_transcribe_success_sends_audio_and_params():
    seen = {}

    def handler(request: httpx.Request):
        seen["request"] = request
        return httpx.Response(200, json=stt_body(" What is habit stacking? "))

    client = make_client(handler, deepgram_keyterms="habit stacking, James Clear")
    assert await client.transcribe(b"audio-bytes") == "What is habit stacking?"

    req = seen["request"]
    assert req.url.path == "/v1/listen"
    assert req.url.params["model"] == "nova-3"
    assert req.url.params["smart_format"] == "true"
    assert req.url.params.get_list("keyterm") == ["habit stacking", "James Clear"]
    assert req.headers["Authorization"] == "Token test-key"
    assert req.content == b"audio-bytes"


@pytest.mark.anyio
async def test_transcribe_empty_and_malformed_return_empty_string():
    client = make_client(lambda r: httpx.Response(200, json=stt_body("")))
    assert await client.transcribe(b"x") == ""

    client = make_client(lambda r: httpx.Response(200, json={"results": {"channels": []}}))
    assert await client.transcribe(b"x") == ""


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_transcribe_http_error_raises_with_status(status):
    client = make_client(lambda r: httpx.Response(status, json={"err_msg": "nope"}))
    with pytest.raises(VoiceError) as exc:
        await client.transcribe(b"x")
    assert exc.value.status == status


@pytest.mark.anyio
async def test_transcribe_timeout_raises():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = make_client(handler)
    with pytest.raises(VoiceError) as exc:
        await client.transcribe(b"x")
    assert exc.value.timeout and exc.value.status is None


# --- synthesize -------------------------------------------------------------


@pytest.mark.anyio
async def test_synthesize_success():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, content=b"MP3", headers={"Content-Type": "audio/mpeg"})

    client = make_client(handler)
    assert await client.synthesize("**Hello** world.") == b"MP3"

    req = seen[0]
    assert req.url.path == "/v1/speak"
    assert req.url.params["model"] == "aura-2-thalia-en"
    assert req.url.params["encoding"] == "mp3"
    assert req.read() == b'{"text":"Hello world."}'


@pytest.mark.anyio
async def test_synthesize_long_text_concatenates_pieces():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=f"[{len(calls)}]".encode())

    client = make_client(handler)
    audio = await client.synthesize("A fairly ordinary sentence for testing. " * 120)
    assert len(calls) > 1
    assert audio == b"".join(f"[{i}]".encode() for i in range(1, len(calls) + 1))


@pytest.mark.anyio
async def test_synthesize_error_and_empty_text():
    client = make_client(lambda r: httpx.Response(429, text="Too Many Requests"))
    with pytest.raises(VoiceError) as exc:
        await client.synthesize("Hello.")
    assert exc.value.status == 429

    calls = []
    client = make_client(lambda r: calls.append(r) or httpx.Response(200, content=b"MP3"))
    with pytest.raises(VoiceError):
        await client.synthesize("   ")
    assert calls == []  # never calls Deepgram with empty text


@pytest.mark.anyio
async def test_stream_speech_yields_chunks_in_order():
    client = make_client(lambda r: httpx.Response(200, content=b"A" * 10_000))
    chunks = [c async for c in client.stream_speech("Hello there.")]
    assert len(chunks) >= 1
    assert b"".join(chunks) == b"A" * 10_000


@pytest.mark.parametrize("text", ["** **", "...", " - ", "**"])
def test_clean_symbols_only_is_empty(text):
    assert clean_for_speech(text) == ""


@pytest.mark.anyio
async def test_warm_up_hits_deepgram_and_never_raises():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(403, json={"err_msg": "Insufficient permissions"})

    await make_client(handler).warm_up()  # 403 is fine: the connection is open
    assert seen == ["/v1/projects"]

    def down(request):
        raise httpx.ConnectError("no network", request=request)

    await make_client(down).warm_up()  # swallowed
