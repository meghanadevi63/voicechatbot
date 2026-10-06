import { useCallback, useEffect, useRef, useState } from "react";
import { speak } from "../api.js";

const MIME = "audio/mpeg";
// Safari 17+ only has ManagedMediaSource; older Safari has neither and uses the fallback.
const MediaSourceImpl = window.MediaSource || window.ManagedMediaSource;
const CAN_STREAM = Boolean(MediaSourceImpl?.isTypeSupported?.(MIME));

// Feed a streamed MP3 response into a MediaSource so playback starts with the
// first chunk (~0.4 s) instead of after the whole reply has arrived (~4 s).
async function streamInto(audio, res, isCurrent) {
  const mediaSource = new MediaSourceImpl();
  audio.disableRemotePlayback = true; // required by ManagedMediaSource
  audio.src = URL.createObjectURL(mediaSource);
  await new Promise((resolve) => mediaSource.addEventListener("sourceopen", resolve, { once: true }));
  const buffer = mediaSource.addSourceBuffer(MIME);

  const queue = [];
  let done = false;
  let started = false;

  // Append queued chunks as one buffer whenever the SourceBuffer is free.
  const pump = () => {
    if (buffer.updating || mediaSource.readyState !== "open") return;
    if (queue.length) {
      const total = queue.reduce((n, c) => n + c.length, 0);
      const joined = new Uint8Array(total);
      let offset = 0;
      for (const c of queue.splice(0)) {
        joined.set(c, offset);
        offset += c.length;
      }
      buffer.appendBuffer(joined);
    } else if (done) {
      mediaSource.endOfStream();
    }
  };

  const firstAppend = new Promise((resolve) =>
    buffer.addEventListener("updateend", () => {
      if (!started) {
        started = true;
        resolve();
      }
      pump();
    })
  );

  const reader = res.body.getReader();
  const readAll = (async () => {
    for (;;) {
      const { value, done: finished } = await reader.read();
      if (!isCurrent()) return reader.cancel();
      if (finished) break;
      queue.push(value);
      pump();
    }
    done = true;
    pump();
  })();

  await Promise.race([firstAppend, readAll]);
  if (isCurrent()) await audio.play();
  await readAll;
}

async function downloadInto(audio, res) {
  audio.src = URL.createObjectURL(await res.blob());
  await audio.play();
}

/**
 * One shared player for spoken replies.
 * `speech` is { id, status: "loading" | "playing" } for the message being spoken, or null.
 * `error` is { id, message } for the last message whose audio failed, or null.
 */
export default function usePlayer() {
  const [speech, setSpeech] = useState(null);
  const [error, setError] = useState(null);
  const session = useRef(null);

  const stop = useCallback(() => {
    const s = session.current;
    session.current = null;
    if (s) {
      s.controller.abort();
      s.audio.pause();
      if (s.audio.src) URL.revokeObjectURL(s.audio.src);
      s.audio.removeAttribute("src");
    }
    setSpeech(null);
  }, []);

  const play = useCallback(
    async (id, text) => {
      stop();
      setError(null);
      const s = { id, controller: new AbortController(), audio: new Audio() };
      session.current = s;
      const isCurrent = () => session.current === s;

      s.audio.addEventListener("playing", () => isCurrent() && setSpeech({ id, status: "playing" }));
      s.audio.addEventListener("ended", () => isCurrent() && stop());
      setSpeech({ id, status: "loading" });

      try {
        const res = await speak(text, s.controller.signal);
        if (!isCurrent()) return;
        await (CAN_STREAM ? streamInto(s.audio, res, isCurrent) : downloadInto(s.audio, res));
      } catch (e) {
        if (!isCurrent() || e.name === "AbortError") return;
        stop();
        setError({
          id,
          message:
            e.name === "NotAllowedError"
              ? "The browser blocked autoplay. Press play to listen."
              : `Couldn't play audio: ${e.message}`,
        });
      }
    },
    [stop]
  );

  useEffect(() => stop, [stop]);

  return { speech, error, play, stop };
}
