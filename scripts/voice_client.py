"""Talk to the hands-free endpoint from the command line, without a browser.

Streams a WAV file to /api/ws/voice in real time like a microphone would, prints
every event with its timing, and saves the spoken reply.

    python scripts/voice_client.py question.wav
    python scripts/voice_client.py question.wav --url ws://localhost:8001/api/ws/voice --out reply.wav

The WAV must be PCM16 mono 16 kHz (what the browser will send). Timings are
measured from the end of the speech in the file.
"""

import argparse
import asyncio
import json
import time
import wave

import websockets

CHUNK_MS = 80
IN_RATE = 16000  # mic audio to the server
OUT_RATE = 24000  # reply audio from the server


def read_pcm(path: str) -> bytes:
    with wave.open(path) as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, IN_RATE):
            raise SystemExit(f"{path}: need PCM16 mono {IN_RATE} Hz WAV")
        return w.readframes(w.getnframes())


async def run(args) -> None:
    audio = read_pcm(args.wav)
    chunk = IN_RATE * 2 * CHUNK_MS // 1000
    silence = b"\x00" * chunk
    reply = bytearray()
    speech_end = None
    done = asyncio.Event()

    def stamp() -> str:
        return f"{(time.perf_counter() - speech_end) * 1000:+7.0f} ms" if speech_end else "    (speaking)"

    async with websockets.connect(args.url, max_size=None) as ws:
        connect_start = time.perf_counter()
        await ws.send(json.dumps({"type": "start", "history": []}))
        # Like the UI: speak only once the server is listening (it opens Flux and Aura-2 first)
        ready = json.loads(await ws.recv())
        if ready.get("type") == "error":
            raise SystemExit(f"Server error: {ready['message']}")
        print(f"Session ready after {(time.perf_counter() - connect_start) * 1000:.0f} ms, speaking...")

        async def mic():
            nonlocal speech_end
            for i in range(0, len(audio), chunk):
                await ws.send(audio[i : i + chunk])
                await asyncio.sleep(CHUNK_MS / 1000)
            speech_end = time.perf_counter()
            # Keep "listening" to silence, like a real mic, until the answer is done
            while not done.is_set():
                await ws.send(silence)
                await asyncio.sleep(CHUNK_MS / 1000)
            await ws.send(json.dumps({"type": "stop"}))

        async def events():
            first_audio = True
            async for msg in ws:
                if isinstance(msg, bytes):
                    if first_audio:
                        print(f"{stamp()}  <audio starts>")
                        first_audio = False
                    reply.extend(msg)
                    continue
                event = json.loads(msg)
                detail = {k: v for k, v in event.items() if k != "type"}
                if event["type"] == "answer_sources":
                    detail = {"sources": len(event["sources"])}
                print(f"{stamp()}  {event['type']:<18} {json.dumps(detail)[:120]}")
                if event["type"] == "answer_done" or event["type"] == "error":
                    done.set()

        sender = asyncio.create_task(mic())
        try:
            await asyncio.wait_for(events(), timeout=args.timeout)
        except TimeoutError:
            print(f"No reply within {args.timeout} s")
        except websockets.ConnectionClosed:
            pass
        done.set()
        await asyncio.gather(sender, return_exceptions=True)

    if reply:
        with wave.open(args.out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(OUT_RATE)
            w.writeframes(bytes(reply))
        print(f"Saved {len(reply) / (OUT_RATE * 2):.1f} s of reply audio to {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", help="PCM16 mono 16 kHz WAV with a spoken question")
    parser.add_argument("--url", default="ws://localhost:8001/api/ws/voice")
    parser.add_argument("--out", default="reply.wav", help="where to save the spoken reply")
    parser.add_argument("--timeout", type=float, default=60)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
