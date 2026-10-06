# Plan: Deepgram Voice Integration

> **Status: on hold.** The frontend has moved from Streamlit to React (`frontend/`, plain JavaScript + Vite). The sections below have been updated for React. In particular, Phase 2 no longer needs a separate `static/voice.html` page: the real-time client becomes part of the React app.

Add voice input and spoken replies to the RAG chatbot, using Deepgram for speech-to-text (STT) and text-to-speech (TTS). The existing RAG pipeline (intent router → Milvus retrieval → Groq LLM) stays as it is; voice is a layer around it.

```
mic ──▶ Deepgram STT ──▶ RAGChain.answer() (unchanged) ──▶ Deepgram TTS ──▶ speaker
```

We deliver this in two phases:

- **Phase 1, push-to-talk.** The user presses a mic button in the React chat, records a question and hears the answer. Small change, no new infrastructure.
- **Phase 2, hands-free real-time.** The user talks naturally, the bot detects when they have finished, answers with low latency, and stops if interrupted. This adds a WebSocket voice session and streaming end to end.

---

## 1. Requirements

### Functional

| # | Requirement | Phase |
|---|-------------|-------|
| F1 | User can ask a question by voice from the chat UI | 1 |
| F2 | The transcribed question appears in the chat as the user's message | 1 |
| F3 | The answer is shown as text (with sources, as today) **and** played as audio | 1 |
| F4 | User can turn spoken replies on/off, including for typed questions | 1 |
| F5 | Voice questions use the same conversation history as typed ones (follow-ups work) | 1 |
| F6 | If nothing intelligible was said, the bot says so instead of querying the RAG | 1 |
| F7 | Hands-free mode: no button press; the bot detects the end of the user's turn | 2 |
| F8 | Barge-in: if the user starts talking while the bot speaks, playback stops and the bot listens | 2 |

### Non-functional

| # | Requirement |
|---|-------------|
| N1 | **Latency.** Phase 1: under 3 s from end of recording to the start of audio. Phase 2: under 1.2 s from end of speech to the first audio. |
| N2 | **Security.** The Deepgram API key stays on the backend and is never sent to the browser. |
| N3 | **Graceful degradation.** If TTS fails, the text answer is still returned. If STT fails, the user gets a clear error and can type instead. |
| N4 | **Config-driven.** Models, voice and language are set in `.env`, like the Groq settings. |
| N5 | **No change to the RAG behaviour.** The router, grounding rules and sources work the same for voice and text. |

### Out of scope (for now)

- Phone calls (Twilio/SIP)
- Speaker identification
- Storing audio recordings
- Indian-language voices (see the language constraint in section 2)

---

## 2. Deepgram models and APIs

| Purpose | Model | Endpoint | Used in |
|---------|-------|----------|---------|
| STT, recorded clip | `nova-3` | `POST https://api.deepgram.com/v1/listen` | Phase 1 |
| STT, live with end-of-turn detection | `flux-general-en` | `wss://api.deepgram.com/v2/listen` | Phase 2 |
| TTS, whole reply | `aura-2-thalia-en` (any Aura-2 voice) | `POST https://api.deepgram.com/v1/speak` | Phase 1 |
| TTS, streamed | same | `wss://api.deepgram.com/v1/speak` (`Speak` / `Flush` / `Clear` / `Close` messages) | Phase 2 |

Why these:

- **Nova-3** is Deepgram's most accurate general STT model. With `smart_format=true` it adds punctuation and formats numbers, which improves the router's search queries.
- **Flux** is built for voice agents. It returns `StartOfTurn`, `EndOfTurn`, `EagerEndOfTurn` and `TurnResumed` events, so we don't need our own voice-activity detection or silence timers.
- **Aura-2** is fast and cheap, and its voices sound natural for short spoken answers. Our prompt already asks for short spoken sentences, which suits TTS.

Constraints to design around:

- **Aura-2 accepts at most 2000 characters per request.** Answers are normally 2–4 sentences, but "more detail" replies can be longer. Split long text at sentence boundaries and synthesise each piece.
- **Languages.** Nova-3 and Flux (`flux-general-multi`) understand Hindi and many other languages, but Aura-2 has no Hindi or Telugu voices. If Indian-language replies are needed, TTS would have to come from another provider. English only for now.
- Use plain HTTP and WebSockets (`httpx` / `websockets`) rather than the Deepgram Python SDK. The SDK's API has changed between major versions, and we only need four calls.

### Alternative considered: Deepgram Voice Agent API

Deepgram also offers an all-in-one agent API (STT + LLM + TTS over one WebSocket). We are not using it because the RAG would have to run as a function call or a custom LLM endpoint. We would lose direct control over the intent router, retrieval and the sources shown in the UI, and it is harder to debug. We can revisit it if Phase 2 turns out to be too much work to build ourselves.

---

## 3. Phase 1: push-to-talk

### 3.1 Config: `backend/config.py`, `.env.example`, README

```python
deepgram_api_key: str = ""
deepgram_stt_model: str = "nova-3"
deepgram_tts_model: str = "aura-2-thalia-en"
deepgram_language: str = "en"
```

### 3.2 New module: `backend/voice.py`

A thin async Deepgram client using `httpx.AsyncClient`. One client is created in the FastAPI `lifespan` and reused.

- `async transcribe(audio: bytes, mimetype: str) -> str`
  - `POST /v1/listen?model=nova-3&smart_format=true&language=en` with the raw audio as the request body.
  - Returns `results.channels[0].alternatives[0].transcript`, or `""` if there is none.
- `async synthesize(text: str) -> bytes`
  - `POST /v1/speak?model=aura-2-thalia-en&encoding=mp3` with `{"text": ...}`.
  - Splits text over about 1900 characters at sentence boundaries and joins the MP3 pieces (MP3 frames can be concatenated directly).
- Both send `Authorization: Token <key>`, use a 15 s timeout, and raise a `VoiceError` on non-2xx responses.

### 3.3 New API endpoints: `backend/main.py`

| Method | Path | Request | Response |
|--------|------|---------|----------|
| POST | `/voice` | multipart: `audio` file + `history` (JSON string) | `{"transcript", "answer", "sources", "audio_b64" \| null}` |
| POST | `/tts` | `{"text": "..."}` | `audio/mpeg` bytes |

`/voice` flow:

1. Transcribe the audio. If the transcript is empty, return `answer = "Sorry, I didn't catch that."` and skip the RAG.
2. Call `RAGChain.answer(transcript, history)` in the thread pool (same as `/chat`).
3. Synthesise the answer. If TTS fails, log it and return `audio_b64 = null` so the text answer still arrives (N3).

`/tts` is used to speak replies to typed questions when spoken replies are on.

New dependency: `python-multipart`, which FastAPI needs for file uploads. `httpx` is already installed through the Groq client; add it to `requirements.txt` explicitly.

### 3.4 Frontend: React (`frontend/src/`)

- **Mic button** in `Composer.jsx`, next to Send. Press to record (`navigator.mediaDevices.getUserMedia` + `MediaRecorder`, WebM/Opus, which Deepgram accepts as-is). Press again to stop and send. Show a recording timer and a cancel option.
- `api.js`: add `sendVoice(blob, history)` (multipart `FormData` to `/voice`) and `speak(text)` (`/tts`, returns a Blob).
- On response: append the transcript as the user message, then the answer and sources, as `ask()` does today. Play the audio with an `Audio` element.
- **"Speak replies"** toggle in `Sidebar.jsx` (saved in `localStorage`, default on). When on, typed questions also call `/tts`.
- A small "speaking" indicator on the assistant message, with a stop button.
- Store only text in the message list. Audio is played and then discarded.

### 3.5 Phase 1 latency estimate

| Step | Approx. |
|------|---------|
| Upload + Nova-3 transcription (5 s clip) | 0.3–0.6 s |
| Router LLM call | 0.2–0.4 s |
| Retrieval (Milvus Lite + MiniLM) | 0.1 s |
| Answer LLM (Groq) | 0.3–0.8 s |
| Aura-2 synthesis (full reply) | 0.3–0.6 s |
| **Total** | **~1.2–2.5 s** (meets N1) |

---

## 4. Phase 2: hands-free real-time

Phase 2 runs inside the React app: a "hands-free" mode on the same mic button, with the conversation flowing into the same chat history.

### 4.1 Architecture

```
Browser (React app)                        FastAPI  /ws/voice                    Deepgram
───────────────────────────               ─────────────────────                ─────────────
mic → AudioWorklet → PCM16 16 kHz ──ws──▶ forward audio ─────────────────────▶ Flux /v2/listen
                                           ◀── StartOfTurn / EndOfTurn ───────
                                           on EndOfTurn:
                                             RAGChain.astream_answer()
                                             → sentence chunker ─── Speak/Flush ─▶ Aura-2 /v1/speak (ws)
speaker ◀─ AudioWorklet ◀── PCM16 ─────ws── forward audio ◀──────────────────
UI: live transcript, answer text, sources ◀── JSON events
```

The backend sits between the browser and Deepgram, so the API key never reaches the browser (N2).

### 4.2 Backend changes

- **Streaming RAG.** Add `RAGChain.astream_answer(question, history)`.
  - Router and retrieval run as today.
  - The answer chain uses `.astream()` and yields tokens.
  - Sources are sent as a separate event before the tokens.
  - Small-talk replies are yielded as a single chunk.
- **Sentence chunker.** Collect tokens until a sentence ends (`.?!` followed by a space, or a newline), then send `{"type": "Speak", "text": ...}` + `Flush` to the TTS WebSocket. The first sentence starts playing while the LLM is still writing the rest; this is the biggest latency gain.
- **`/ws/voice` session.** One Flux connection and one Aura-2 connection per browser session, run as asyncio tasks:
  - `mic_pump`: browser → Flux.
  - `stt_events`: reads Flux events. On `EndOfTurn` it starts an answer task.
  - `answer_task`: RAG stream → chunker → TTS.
  - `tts_pump`: Aura-2 audio → browser.
- **Barge-in (F8).** On `StartOfTurn` while an answer is in progress:
  - cancel `answer_task`;
  - send `{"type": "Clear"}` to TTS;
  - send `{"event": "stop_playback"}` to the browser.
- **History.** The server keeps conversation history for each session. Barge-in can cut a reply short; store only the part that was actually spoken.
- **Audio formats.**
  - Mic: `linear16`, 16 kHz, 80 ms chunks (Flux recommendation).
  - TTS: `linear16`, 24 kHz, played via an AudioWorklet so it can start and stop without gaps.
- **Optional optimisation.** Use `EagerEndOfTurn` to start retrieval early, and discard the result on `TurnResumed`.

### 4.3 Browser client: React

- `src/voice/useVoiceSession.js`: a hook that owns the WebSocket, mic AudioWorklet (PCM16, 16 kHz, 80 ms chunks) and playback AudioWorklet. It exposes `status` (idle / listening / thinking / speaking), the live transcript and `start()` / `stop()`.
- Worklet processors in `public/worklets/` (plain JS, loaded with `audioContext.audioWorklet.addModule`).
- UI: the mic button toggles hands-free mode, plus a mic level meter and a status label. Transcripts and answers are appended to the normal message list, with sources.
- In dev, add `/ws` to the Vite proxy with `ws: true`.

### 4.4 Phase 2 latency target

| Step | Target |
|------|--------|
| Flux end-of-turn detection after the user stops | ~0.2–0.4 s |
| Router + retrieval | ~0.3–0.5 s |
| First LLM sentence | ~0.2–0.3 s |
| Aura-2 first audio | ~0.2 s |
| **End of speech → first audio** | **~0.9–1.2 s** |

If the router is the bottleneck, consider skipping it for obviously document-related questions, or running it in parallel with retrieval on the raw transcript.

---

## 5. Files touched

| File | Phase | Change |
|------|-------|--------|
| `backend/config.py` | 1 | Deepgram settings |
| `backend/voice.py` | 1 (+2) | New: Deepgram REST client (Phase 1), WebSocket helpers (Phase 2) |
| `backend/main.py` | 1 (+2) | `/voice`, `/tts`; later `/ws/voice` |
| `backend/rag.py` | 2 | `astream_answer()` |
| `frontend/src/components/Composer.jsx`, `Sidebar.jsx`, `api.js` | 1 | Mic button, "Speak replies" toggle, voice API calls |
| `frontend/src/voice/`, `frontend/public/worklets/` | 2 | Real-time voice hook and audio worklets |
| `frontend/vite.config.js` | 1 (+2) | Proxy `/voice`, `/tts`; later `/ws` |
| `requirements.txt` | 1 | `httpx`, `python-multipart` (`websockets` already comes with `uvicorn[standard]`) |
| `.env.example`, `README.md` | 1 | New variables, API table, usage |

---

## 6. Testing

- **Unit.**
  - `voice.transcribe` / `synthesize` with mocked `httpx` responses: success, empty transcript, 401, 429, timeout.
  - The sentence chunker, including abbreviations ("e.g."), decimals and very long sentences.
- **Integration (needs a key, skipped in CI otherwise).**
  - Send a short WAV of a known question through `/voice`. Check that the transcript contains the expected terms and that the audio is non-empty MP3.
- **Manual checklist.**
  - Voice question with a follow-up ("what about the second one?").
  - Small talk ("thanks!").
  - Silence or noise only.
  - TTS key removed (text answer still shown).
  - Long "explain in detail" answer (over 2000 characters).
  - Phase 2: interrupting mid-answer, and pausing mid-sentence (should not cut the user off).
- **Latency logging.** Log the time of each step (STT, router, retrieval, LLM, TTS) per request so N1 can be measured, not guessed.

---

## 7. Open questions

1. **Languages.** Is English enough? If users will speak Hindi or Telugu, STT works with Nova-3/Flux multilingual, but TTS needs another provider.
2. **Voice.** Which Aura-2 voice (e.g. `thalia`, `asteria`, `apollo`, or a British/Australian accent)? We could add a voice picker in the UI.
3. **Is Phase 2 needed now?** Or is push-to-talk enough for the first release?
4. **Deployment.** Will this run over HTTPS? Browsers allow microphone access only on `https://` or `localhost`.
5. **Cost and limits.** Check current Deepgram pricing, concurrency limits and free credit before choosing the account plan.

---

## 8. Milestones

1. **Phase 1a:** config + `backend/voice.py` + `/voice` and `/tts` endpoints, tested with `curl`.
2. **Phase 1b:** React mic button, playback, "Speak replies" toggle; manual checklist passes.
3. **Phase 2a:** `astream_answer()` + sentence chunker + `/ws/voice` with Flux and Aura-2 streaming (no barge-in).
4. **Phase 2b:** React voice hook + worklets, barge-in, latency logging, tuning (`eot_threshold`, eager end-of-turn).
