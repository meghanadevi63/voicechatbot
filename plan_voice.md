# Plan: Voice Assistant for the Docs Chatbot

Add voice to the existing RAG chatbot: users can ask by speaking and hear the answer. Deepgram handles speech-to-text (STT) and text-to-speech (TTS). The RAG pipeline (intent router → Milvus retrieval → Groq LLM) stays the same; voice wraps around it.

```
mic ──▶ Deepgram STT ──▶ RAGChain (unchanged logic) ──▶ Deepgram TTS ──▶ speaker
                              │
                              └──▶ text answer + sources in the chat UI (as today)
```

## Decisions

| Topic | Decision |
|-------|----------|
| Scope | **Phase 1:** push-to-talk. **Phase 2:** hands-free real-time with barge-in |
| Provider | Deepgram for both STT and TTS |
| Language | English only |
| Architecture | Cascaded pipeline (STT → our RAG → TTS) that we control, not an all-in-one voice-agent API |
| Phase 2 answers | Start with the existing whole-answer `answer()`. Stream the LLM answer only if the timing logs show it's needed |

### Why a cascaded pipeline, not an all-in-one voice agent

All-in-one APIs (Deepgram Voice Agent, OpenAI Realtime, Gemini Live) would make our RAG a tool call made by someone else's LLM. We would lose direct control of the intent router, the grounding rules and the sources panel, and debugging gets harder. With a cascaded pipeline, every step can be swapped, logged and timed. Phase 2 also gets close to the latency of the all-in-one APIs (about 1 s).

### Models

| Purpose | Model | Endpoint | Phase |
|---------|-------|----------|-------|
| STT, recorded clip | `nova-3` | `POST https://api.deepgram.com/v1/listen` | 1 |
| STT, live with end-of-turn detection | `flux-general-en` | `wss://api.deepgram.com/v2/listen` | 2 |
| TTS, whole reply | `aura-2-thalia-en` (any Aura-2 English voice) | `POST https://api.deepgram.com/v1/speak` | 1 |
| TTS, streamed | same | `wss://api.deepgram.com/v1/speak` | 2 |

- **Nova-3:** Deepgram's most accurate batch STT model. `smart_format=true` adds punctuation and formats numbers, which helps the router write better search queries. `keyterm` can boost domain words from the documents (e.g. names and jargon).
- **Flux:** STT built for voice agents. It emits `StartOfTurn`, `Update`, `EagerEndOfTurn`, `TurnResumed` and `EndOfTurn`, so we need no VAD or silence timers of our own.
- **Aura-2:** fast, cheap TTS with natural English voices. Our system prompt already asks for short spoken sentences with no markdown, which suits it.

### Deepgram limits to design around

- Aura-2 accepts **at most 2000 characters per request**, so split longer text at sentence boundaries.
- Streaming TTS allows **at most 20 `Flush` messages per 60 s** and 2400 characters per minute per connection. The Phase 2 chunker must group sentences instead of flushing after each one.
- Flux wants raw audio in **80 ms chunks** (`linear16` at 16 kHz is 2560 bytes per chunk).
- Use the official **`deepgram-sdk`, pinned to `7.12.0`** (latest stable as of 2026-10-02). It has had four major versions in a year (v4 May 2025 → v7 Apr 2026), so all SDK calls stay inside `backend/voice.py` and upgrades touch only that file.
- **Phase 2 probe (2026-10-07, SDK 7.12.0, our key).** Flux and Aura-2 WebSocket both work.
  - Flux: `EndOfTurn` arrived **~190 ms** after the user stopped speaking, and a short pause mid-question ("…stacking? And how…") did not end the turn. Connecting takes ~1 s, so open both sockets when hands-free starts, never per turn.
  - Aura-2 WebSocket: first audio **~300 ms** after `Flush`; normally 8 s of speech arrives within ~2.5 s, with gaps ≤ 70 ms. The player needs a small jitter buffer (~150 ms) before it starts.
  - **Outliers:** in 2 of 5 runs the audio stalled for seconds (one run took 34 s for 8 s of speech). Phase 2 needs a stall timeout: if no audio arrives for ~3 s, end the answer with a message and keep the text visible.
- **Aura-2 is slower than expected for a whole reply.** Measured on 2026-10-06: first audio after ~0.4 s (warm), but ~3–4 s for a two-sentence reply. Audio must be **streamed** to the browser and played as it arrives, never fully buffered first.

---

## How the current code is affected

| Area | Today | Impact |
|------|-------|--------|
| `backend/rag.py` prompts | Already written for speech ("will be read aloud", 2–4 sentences) | No prompt changes |
| `RAGChain.answer()` | Synchronous and returns the whole answer | Fine for Phase 1 and for Phase 2 at first (run in a threadpool). An async streaming version is optional, later (2a-2) |
| Intent router | An extra Groq call before every answer (~0.2–0.4 s) | Acceptable. Phase 2 can overlap it with Flux eager end-of-turn |
| Conversation history | Stored in the browser and sent with every `/api/chat` | Phase 1 reuses this as is. Phase 2 seeds a server-side session from it |
| `Composer.jsx` | Only a Send button | Add a mic button |
| `vite.config.js` | Proxied each API path separately | *Done:* all routes moved under `/api`; one proxy entry `/api` with `ws: true` covers Phase 2 too |
| Deployment | One FastAPI server that also serves `frontend/dist` | Browsers allow the mic only on **HTTPS or localhost** |

---

## Phase 1: push-to-talk

### User experience

1. The user clicks the mic button (or holds Space while focus is outside fields and buttons, or the message box is empty; a tap under 250 ms does nothing), speaks, then clicks again or releases Space to stop. A timer and a cancel button are shown. Recording stops automatically after 60 s.
2. The transcript appears as the user's message, exactly like a typed question.
3. The text answer and its sources appear as they do today, and the answer is read aloud.
4. A **"Speak replies"** toggle in the sidebar (saved in `localStorage`, on by default) also speaks answers to typed questions.
5. A stop button on the assistant message stops playback. Sending a new question also stops it.
6. If nothing intelligible was said, show "Sorry, I didn't catch that" and don't call the RAG.

### Design: two small endpoints that the frontend composes

The frontend chains the existing pieces: **`/api/stt` → existing `ask()` / `/api/chat` → `/api/tts`**. This is better than one combined `/voice` endpoint because:

- the existing `/api/chat` path, history handling and error handling are reused unchanged;
- the transcript shows as soon as STT finishes, and the answer text shows before the audio is ready;
- typed and spoken questions share one TTS path.

The cost is one extra browser↔backend round trip of a few milliseconds.

### Backend

**Config (`backend/config.py`, `.env.example`, README)**

```python
deepgram_api_key: str = ""
deepgram_stt_model: str = "nova-3"
deepgram_tts_model: str = "aura-2-thalia-en"
deepgram_keyterms: str = ""          # optional, comma-separated domain terms for Nova-3
```

**New module `backend/voice.py`.** A `DeepgramClient` wrapping the SDK's `AsyncDeepgramClient` (15 s timeout, 1 retry). One instance is created in `lifespan` and stored in `state`. *(Done: step 1.)*

- `async transcribe(audio: bytes, content_type: str) -> str`
  - Calls `listen.v1.media.transcribe_file(model=nova-3, smart_format=True, language=en, keyterm=...)`.
  - Deepgram detects the container from the bytes (WebM/Opus from Chrome and Firefox, MP4/AAC from Safari).
  - Returns `results.channels[0].alternatives[0].transcript`, or `""` when there is none.
- `async stream_speech(text) -> AsyncIterator[bytes]` (and `synthesize(text) -> bytes`, which joins it)
  - Runs `clean_for_speech(text)` first: removes any leftover markdown (`*`, `#`, backticks, links) and collapses whitespace.
  - Splits the text into pieces under ~1900 characters at sentence boundaries.
  - Calls `speak.v1.audio.generate(model=aura-2-thalia-en, encoding=mp3)` once per piece and yields MP3 chunks as they arrive (MP3 frames from consecutive pieces can simply follow each other).
- SDK and network errors become `VoiceError`, keeping the Deepgram status so 401 and 429 can be told apart.

**Endpoints (`backend/main.py`)**

| Method | Path | Request | Response |
|--------|------|---------|----------|
| POST | `/api/stt` | multipart `audio` file (max 10 MB) | `{"transcript": "..."}` |
| POST | `/api/tts` | `{"text": "..."}` (max 5000 chars) | **streamed** `audio/mpeg` (chunked) |
| POST | `/api/voice/warmup` | – | 204. Re-opens the Deepgram connection (see below) |

- Return 503 with a clear message if `DEEPGRAM_API_KEY` is not set. Voice is optional, and the text chat keeps working without it.
- Map Deepgram errors as follows:
  - 401 → 502 "Voice service misconfigured"
  - 429 → 503 "Voice service busy"
  - timeout → 504
- Log how long each call takes (`stt_ms`, `tts_ms`).
- **Connection warm-up.** Deepgram drops idle connections after a few seconds, and reconnecting adds ~0.55 s (measured: TTS first byte 0.95 s cold vs 0.39 s warm; a longer httpx keepalive doesn't help). The frontend calls `/api/voice/warmup` when it sends a question, so the connection is warm again by the time the answer is ready. Step 4 also calls it when recording starts. *(Done: step 3.)*
- `/api/tts` reads the first chunk *before* returning the `StreamingResponse`, so a Deepgram error still becomes a proper HTTP error status instead of a broken stream.
- New dependencies in `requirements.txt`: `deepgram-sdk==7.12.0`, `httpx` (pinned explicitly) and `python-multipart`, which FastAPI needs for uploads.

### Frontend (`frontend/src/`)

- **`voice/useRecorder.js`:** a hook wrapping `getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}})` and `MediaRecorder`.
  - Exposes `state` (idle / recording / error), `elapsed`, `start()`, `stop() → Blob` and `cancel()`.
  - Releases the mic tracks after every recording so the browser's recording indicator turns off.
- **`voice/usePlayer.js`:** a single shared `Audio` element.
  - Exposes `play(blob)`, `stop()` and `playingId` (which message is speaking).
  - Revokes object URLs after use.
- **`api.js`:** add `transcribe(blob)` (`FormData` → `/api/stt`) and `speak(text)` (`/api/tts`, returns a Blob).
- **`App.jsx`:**
  - Add `askByVoice(blob)`: transcribe, then call the existing `ask(transcript)`.
  - After an assistant answer arrives, if "Speak replies" is on, call `speak()` and then `player.play()`.
  - TTS failures are logged and shown as a small inline note. The text answer is never lost.
- **`Composer.jsx`:** add a mic button next to Send that switches to a stop/cancel bar with a timer while recording. Disable it while a request is pending.
- **`Sidebar.jsx`:** add the "Speak replies" toggle.
- **`ChatMessage.jsx`:** show a speaking indicator with a stop button, plus a replay button on assistant messages.
- Messages store only text. Audio is never saved.
- **Mic permission errors:** show a plain message ("Microphone blocked: allow it in the browser's site settings") and keep text input available.

### Phase 1 latency estimate (end of recording → first audio)

| Step | Approx. |
|------|---------|
| Upload + Nova-3 (5 s clip) | 0.3–0.6 s |
| Router LLM (Groq) | 0.2–0.4 s |
| Retrieval (Milvus Lite + MiniLM, CPU) | ~0.1 s |
| Answer LLM (Groq) | 0.3–0.8 s |
| Aura-2 first audio (streamed + warm-up; full reply takes ~3–4 s) | ~0.4 s (measured in Chrome: playback starts 0.36–0.52 s after the answer text) |
| **Total** | **~1.3–2.3 s** (target < 3 s) |

---

## Phase 2: hands-free real-time

The mic button gets a second mode: **hands-free**. The user talks naturally, the bot detects the end of each turn, starts speaking the first sentence while the LLM is still generating, and stops immediately if the user interrupts. Everything still flows into the same chat message list with sources.

### Architecture

```
Browser (React)                              FastAPI  /api/ws/voice                      Deepgram
────────────────                             ──────────────────                         ────────
mic → AudioWorklet → PCM16 16 kHz, 80 ms ─ws─▶ mic_pump ──────────────────────────────▶ Flux /v2/listen
                                              stt_events ◀── StartOfTurn/Update/EndOfTurn ─
                                                on EndOfTurn → answer_task:
                                                  RAGChain.answer() (whole answer;
                                                  astream_answer() later, optional)
                                                  → sentence chunker ── Speak/Flush ───▶ Aura-2 /v1/speak (ws)
speaker ◀─ AudioWorklet ◀── PCM16 24 kHz ───ws─ tts_pump ◀──────────── audio ──────────
UI ◀── JSON events (status, partial transcript, answer text, sources, stop_playback) ──
```

The backend sits between the browser and Deepgram, so the API key never reaches the browser.

### WebSocket protocol (`/api/ws/voice`)

**Browser → server**
- Binary frames: mic audio (PCM16 mono, 16 kHz, 80 ms per frame).
- JSON `{"type": "start", "history": [...]}`: the first message. It seeds the server-side history from the current chat.
- JSON `{"type": "stop"}`: ends the session.

**Server → browser**
- Binary frames: TTS audio (PCM16 mono, 24 kHz).
- JSON events:

| Event | Meaning |
|-------|---------|
| `status` | `listening` / `thinking` / `speaking` |
| `partial_transcript` | Live text from Flux `Update` |
| `user_turn` | Final transcript of the user's turn. The UI appends it as a user message |
| `answer_sources` | Sources for the current answer (sent before the text) |
| `answer_delta` | Next piece of answer text (streams into the assistant message). With the whole-answer `answer()`, the full text arrives as one delta |
| `answer_done` | Answer finished; carries `interrupted: bool` |
| `stop_playback` | Barge-in: the browser must drop its queued audio now |
| `error` | Message the UI shows, after which the session ends |

### Backend

- **Answers, first version (2a-1):** `answer_task` calls the existing `RAGChain.answer()` in `run_in_threadpool` and gets the whole answer at once. It sends `answer_sources`, then the full text as one `answer_delta`, then sends the text to Aura-2 as one `Speak` + `Flush` (split at ~1900 characters if longer). No changes to `rag.py`.
  - Barge-in can stop the *speech* but not the LLM call, which runs in a thread and finishes anyway. That's acceptable because answers are short.
- **Optional later (2a-2), only if the timing logs show the full answer wait is too slow:** `RAGChain.astream_answer(question, history)`, an async generator that yields `("sources", [...])` and then `("delta", text)`.
  - Router: uses `ainvoke`.
  - Retrieval: Milvus Lite is synchronous, so it runs in `run_in_threadpool`.
  - Answer: `self.chain.astream(...)`.
  - Small-talk replies are yielded as a single delta.
  - `answer()` stays as it is for `/api/chat`. Shared helpers keep the two from drifting apart.
  - Expected gain: ~0.3–0.6 s, and barge-in can then also stop generation.
- **Sentence chunker** (`backend/voice.py`, needed only with 2a-2): collects tokens until a sentence ends (`.?!` followed by whitespace, or a newline). It skips abbreviations ("e.g.", "Dr.") and decimals ("3.5").
  - The **first sentence** is sent immediately (`Speak` + `Flush`), because that is the latency win.
  - Later sentences are sent with `Speak` as they arrive, but flushed only every ~200 characters and at the end. This stays under the 20-flushes-per-minute limit.
- **Session tasks** (one Flux socket and one Aura-2 socket per browser session, all under one `asyncio.TaskGroup`):
  - `mic_pump`: browser audio → Flux.
  - `stt_events`: Flux events → UI, and starts `answer_task` on `EndOfTurn`.
  - `answer_task`: RAG stream → chunker → TTS, and text deltas → UI.
  - `tts_pump`: Aura-2 audio → browser.
- **Barge-in:** when Flux sends `StartOfTurn` while an answer is being spoken:
  1. cancel `answer_task`;
  2. send `{"type": "Clear"}` to Aura-2;
  3. send `stop_playback` to the browser.
- **Echo guard.** The bot's own voice can reach the mic and trigger a false barge-in. Rely on browser echo cancellation, and only treat `StartOfTurn` as barge-in if the turn has at least ~2 words or lasts longer than ~300 ms. Test with laptop speakers, not only headphones.
- **History:** the server keeps each session's history.
  - When an answer is interrupted, store only the text that was flushed to TTS, so the LLM doesn't think it said more than it did.
  - The browser appends `user_turn` and the answer to its own message list, so a typed follow-up after hands-free mode still has context.
- **Origin check:** browsers don't apply CORS to WebSockets, so `/api/ws/voice` must check the `Origin` header itself: accept same-origin plus `CORS_ORIGINS`, and reject anything else before opening Deepgram sockets.
- **Cleanup:** when the browser disconnects or sends `stop`, close both Deepgram sockets and cancel the tasks.
  - Limit hands-free sessions per server with a semaphore, as protection against runaway Deepgram costs.
- **Optional (Phase 2b):** set `eager_eot_threshold` so that `EagerEndOfTurn` starts the router and retrieval early. Discard that work on `TurnResumed`, and reuse it on `EndOfTurn` when the transcript is unchanged. This saves ~0.3 s.

New config: `deepgram_flux_model = "flux-general-en"`, `deepgram_eot_threshold = 0.7`, `deepgram_eager_eot_threshold` (empty = off), `voice_max_sessions = 5`.

### Frontend

- **`voice/useVoiceSession.js`:** owns the WebSocket, the mic worklet and the playback worklet.
  - Exposes `status`, `partialTranscript`, `level` (for a mic meter), `start()` and `stop()`.
  - Feeds `user_turn` / `answer_*` events into the same message list that `App.jsx` manages.
- **`public/worklets/mic-processor.js`:** downmixes to mono, **resamples in the worklet** from the device rate (usually 48 kHz) to 16 kHz, converts to PCM16, and posts 80 ms chunks. Firefox can't connect a mic stream to an `AudioContext` with a different sample rate, so we resample here instead of relying on that.
- **`public/worklets/player-processor.js`:** a ring buffer of PCM16 at 24 kHz.
  - A `flush` message empties it instantly (for barge-in).
  - It reports when playback actually ends so the status can return to `listening`.
- **UI:**
  - The mic button gets a push-to-talk / hands-free mode switch.
  - In hands-free mode, show a status pill (Listening / Thinking / Speaking), a mic level meter, and the live partial transcript in grey above the composer.
- **`vite.config.js`:** nothing to do; the `/api` proxy already has `ws: true`.

### Phase 2 latency target (end of user speech → first audio)

| Step | 2a-1 (whole answer) | 2a-2 (streamed answer) |
|------|---------------------|------------------------|
| Flux end-of-turn detection | 0.2–0.4 s | 0.2–0.4 s |
| Router + retrieval | 0.3–0.5 s | 0.3–0.5 s (less with eager EOT) |
| LLM | whole answer: 0.3–0.8 s | first sentence: 0.2–0.3 s |
| Aura-2 first audio | ~0.2 s | ~0.2 s |
| **Total** | **~1.0–1.9 s** | **~0.9–1.2 s** |

The timing logs (milestone 0) decide whether 2a-2 is worth doing.

### Fallback if Phase 2 becomes too much work

**Pipecat** (open source, Python) already handles transport, turn-taking, barge-in and Deepgram Flux/Aura services. Our RAG would plug in as a custom processor. Decide this at the start of Phase 2a. If the hand-built version isn't working end to end (without barge-in) within about two days, switch.

---

## Cross-cutting

- **Security.**
  - The Deepgram key is used only on the backend.
  - `/api/stt`, `/api/tts` and `/api/ws/voice` spend paid credits. If the app is ever exposed beyond localhost or a trusted network, add authentication or rate limiting first.
- **HTTPS.** Needed for the mic on any host other than localhost. For LAN demos, use a reverse proxy with a certificate (e.g. Caddy) or `vite --https`.
- **Graceful degradation.**
  - No key / STT error: the mic button shows an error, and typing still works.
  - TTS error: the text answer is still shown.
  - Hands-free socket drops: return to push-to-talk with a message.
- **Observability.** Log one line per turn with `stt_ms`, `router_ms`, `retrieval_ms`, `llm_first_token_ms`, `llm_total_ms`, `tts_first_audio_ms`. This lets us measure the latency targets rather than guess them.
- **Cost.** Check current Deepgram pricing, concurrency limits and free credit before choosing a plan. Phase 2 bills STT for the whole time hands-free is on, not only while the user is speaking. Auto-stop hands-free after ~2 minutes of silence.

---

## Files touched

| File | Phase | Change |
|------|-------|--------|
| `backend/config.py`, `.env.example`, `README.md` | 1 (+2) | Deepgram settings, API table, usage |
| `backend/voice.py` | 1 (+2) | New. REST `transcribe` / `synthesize` and `clean_for_speech`; later the chunker and WebSocket session |
| `backend/main.py` | 1 (+2) | `/api/stt`, `/api/tts`, httpx client in `lifespan`; later `/api/ws/voice` |
| `backend/rag.py` | 0 (+2a-2) | Timing logs (`router_ms`, `retrieval_ms`, `llm_ms`); later, optionally, `astream_answer()` with shared helpers |
| `requirements.txt` | 1 | `httpx`, `python-multipart` (`websockets` already comes with `uvicorn[standard]`) |
| `frontend/src/api.js` | 1 | `transcribe`, `speak` |
| `frontend/src/voice/useRecorder.js`, `usePlayer.js` | 1 | New |
| `frontend/src/voice/useVoiceSession.js` | 2 | New |
| `frontend/public/worklets/*.js` | 2 | New. Mic and player processors |
| `frontend/src/App.jsx`, `components/Composer.jsx`, `Sidebar.jsx`, `ChatMessage.jsx`, `icons.jsx`, `styles.css` | 1 (+2) | Mic button, recording bar, speak toggle, speaking indicator; later hands-free UI |
| `frontend/vite.config.js` | 1 | Single `/api` proxy entry with `ws: true` (done) |
| `tests/` | 1 (+2) | New. See below |

---

## Testing

**Unit (pytest, no network)**
- `transcribe` / `synthesize` with mocked httpx responses: success, empty transcript, 401, 429, timeout.
- `synthesize` splitting of text over 2000 characters.
- `clean_for_speech`: strips markdown and keeps sentence punctuation.
- Sentence chunker (2a-2): abbreviations, decimals, a very long sentence with no punctuation, and the flush limit.
- `astream_answer` with a fake LLM (2a-2): small-talk path, document path, and sources arriving before deltas.

**Integration (needs `DEEPGRAM_API_KEY`, skipped otherwise; add before merging, not needed now)**
- Send a short WAV of a known question ("What is habit stacking?") to `/api/stt`: the transcript contains the key terms.
- `/api/tts` returns non-empty `audio/mpeg`.

**Manual checklist**
- Voice question, then a voice follow-up ("what about the second law?"), then a typed follow-up. All three keep context.
- Small talk ("thanks!") gets a short reply with no sources.
- Silence or only noise → "didn't catch that", with no RAG call.
- Mic permission denied; no Deepgram key; wrong key.
- A long "explain in detail" answer (over 2000 characters) is spoken fully.
- Chrome, Firefox and Safari (recording formats differ). Done before merging.
- Phase 2:
  - interrupt mid-answer: playback stops within about 300 ms;
  - pause mid-sentence: the bot should not cut the user off;
  - laptop speakers with no headphones: no false barge-in;
  - close the tab mid-answer: the server cleans up its sockets.

---

## Milestones

1. ✅ **1a. Backend voice.** Config, `backend/voice.py`, `/api/stt` and `/api/tts`, and unit tests. Verified with `curl`.
2. ✅ **1b. Push-to-talk UI.** Recorder and player hooks, mic button, "Speak replies" toggle, speaking indicator. Measured in Chrome (fake mic): transcript 0.96 s after stopping, spoken answer starts 2.83 s after stopping (target < 3 s).
Phase 2 work happens on the branch `feature/voice-handsfree` (from `feature/deepgram-voice`) and is merged once it works.

3. ✅ **0. Timing logs and hold Space to talk.** Log `router_ms`, `retrieval_ms` and `llm_ms` per turn in `rag.py`, so latency is measured, not guessed. First measurement: a warm document question takes ~2 s end to end, more than the 0.6–1.3 s estimated. Hold Space to talk (`voice/useHoldToTalk.js`), tested in headless Chrome with a fake mic.
4. ✅ **2a-1. Real-time backend, whole answers.** Done: `backend/voice_session.py`, `/api/ws/voice`, `scripts/voice_client.py`, 16 tests with fake sockets. Measured with the client script (3 runs, "What is habit stacking?"): end of speech → user turn **0.45–0.5 s** (includes the WAV's trailing silence), RAG **1.2–1.4 s**, first audio **1.9–2.4 s** after the end of speech. Opening a session takes ~1.7 s. The RAG is now the biggest part of the wait. Plan as written: `/api/ws/voice`: Flux (`flux-general-en`) for live STT and end of turn, the existing `answer()`, and Aura-2 over WebSocket (linear16, 24 kHz). Origin check, session limit, cleanup. No barge-in yet. Tested with a Python client script that streams a WAV file. First check that the Deepgram key has Flux access and that `deepgram-sdk` 7.12.0 supports `/v2/listen`. *Decision point: continue by hand or switch to Pipecat.*
5. ✅ **2b. Hands-free UI.** Done: `useVoiceSession`, mic/player worklets, barge-in, status bar. Tested in headless Chrome with a fake mic: full turn, barge-in while speaking and while thinking, "Stop." alone stops without being answered.
   - Changes from the plan: the browser reports `playback_done`, because audio arrives several times faster than it plays and only the browser knows when the speaker is quiet. Stop words ("stop", "wait", ...) interrupt on their own, since the 2-word echo guard otherwise ignored a plain "Stop.". Mic audio from before the session is ready (2–6 s to open Deepgram) is buffered and sent, not dropped.
   - After an interruption the server history keeps the whole answer, not just the part that was heard (answers aren't streamed yet).
   - Not tested yet: real laptop speakers (echo), Firefox and Safari.
   Original plan: `useVoiceSession`, worklets, barge-in with echo guard, status UI.
6. **2a-2. Streamed answers (optional).** `astream_answer` and the sentence chunker, only if the timing logs show the whole-answer wait is too slow. Target: about 1.2 s or less.
7. **2c. Tuning.** `eot_threshold`, eager end-of-turn, voice choice, silence auto-stop.
8. **Before merging.** Integration tests against real Deepgram, the manual checklist, and Firefox and Safari.

## Later (out of scope now)

- **Multilingual.** STT is easy: Nova-3 `language=multi` (60+ languages, including Indian ones) and `flux-general-multi` (~10 languages, including Hindi). TTS is the gap: Aura-2 covers only English, Spanish, German, French, Dutch, Italian and Japanese. Indian languages would need a second TTS provider such as Sarvam Bulbul or ElevenLabs. Retrieval would also need the router to write the search query in English, or a multilingual embedding model (the current `all-MiniLM-L6-v2` is English-only).
- Phone calls (Twilio/SIP), speaker identification, storing recordings.
- A voice picker in the UI (any Aura-2 English voice works through `DEEPGRAM_TTS_MODEL`).
