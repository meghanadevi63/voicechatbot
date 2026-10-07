import { useEffect, useRef, useState } from "react";

const HOLD_DELAY_MS = 250; // a quick tap of Space doesn't start the mic

// Space starts talking only when it wouldn't type or press something:
// focus is outside fields and buttons, or in the composer while it's empty.
function spaceCanStart(target, composer) {
  if (composer && target === composer) return !composer.value;
  return !(target.isContentEditable || target.closest?.("input, textarea, select, button, a"));
}

/**
 * Hold Space to talk: hold to record, release to send.
 * `recorder` comes from useRecorder; `start()` begins a recording.
 * Returns true while a recording started by holding Space is in progress.
 *
 * hold.state: off | waiting (pressed, before HOLD_DELAY_MS) | holding |
 *             release / abort (waiting for the recorder to finish) |
 *             swallow (recording ended while Space is still down)
 */
export default function useHoldToTalk({ recorder, canStart, start, composerRef }) {
  const [holding, setHolding] = useState(false);
  const hold = useRef({ state: "off", timer: null });
  const live = useRef(null);
  live.current = { recorder, canStart, start };

  // Space can be released while the mic is still starting: stop it once it's recording.
  useEffect(() => {
    const h = hold.current;
    if (recorder.status === "recording") {
      if (h.state === "release") recorder.stop();
      if (h.state === "abort") recorder.cancel();
    } else if (recorder.status === "idle" && !["off", "waiting"].includes(h.state)) {
      // Ended by release, Esc, the 60 s limit or a mic error. If Space is still
      // down, ignore it until it's let go so it doesn't type spaces.
      h.state = h.state === "holding" ? "swallow" : "off";
      setHolding(false);
    }
  }, [recorder.status]);

  useEffect(() => {
    const h = hold.current;
    const reset = () => {
      clearTimeout(h.timer);
      h.state = "off";
      setHolding(false);
    };

    const onKeyDown = (e) => {
      if (e.code !== "Space") return;
      if (h.state !== "off") return e.preventDefault(); // key repeat while held
      if (e.repeat || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (!live.current.canStart || !spaceCanStart(e.target, composerRef.current)) return;
      e.preventDefault();
      h.state = "waiting";
      h.timer = setTimeout(() => {
        if (!live.current.canStart) {
          h.state = "swallow";
          return;
        }
        h.state = "holding";
        setHolding(true);
        live.current.start();
      }, HOLD_DELAY_MS);
    };

    const onKeyUp = (e) => {
      if (e.code !== "Space" || h.state === "off") return;
      e.preventDefault(); // also stops Space "clicking" the focused send button
      const { recorder } = live.current;
      if (h.state === "holding" && recorder.status !== "idle") {
        clearTimeout(h.timer);
        h.state = "release";
        recorder.stop(); // no-op while the mic is still starting; the effect above handles that
      } else if (!["release", "abort"].includes(h.state)) {
        reset();
      }
    };

    // Switching windows mid-hold: the keyup never arrives, so discard the recording.
    const onBlur = () => {
      const { recorder } = live.current;
      if (h.state === "holding" && recorder.status !== "idle") {
        h.state = "abort";
        recorder.cancel();
      } else if (!["release", "abort"].includes(h.state)) {
        reset();
      }
    };

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", onBlur);
    return () => {
      clearTimeout(h.timer);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("blur", onBlur);
    };
  }, [composerRef]);

  return holding;
}
