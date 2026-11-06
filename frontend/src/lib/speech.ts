import { useEffect, useRef, useState } from "react";

/**
 * Read-aloud question card with word highlighting, ported from the original
 * interview room. Autoplays once per turn when enabled.
 */
export function useSpeech(text: string, turnId: number, autoplay: boolean) {
  const [speaking, setSpeaking] = useState(false);
  const [supported] = useState(() => "speechSynthesis" in window);
  const wordsRef = useRef<HTMLSpanElement[]>([]);
  const statusRef = useRef<HTMLSpanElement | null>(null);

  const stop = () => {
    if (!supported) return;
    window.speechSynthesis.cancel();
    wordsRef.current.forEach((span) => span.classList.remove("speaking-word"));
    setSpeaking(false);
    if (statusRef.current) statusRef.current.textContent = "Read or listen, then record your answer.";
  };

  const speak = () => {
    if (!supported) {
      if (statusRef.current) statusRef.current.textContent = "Read-aloud is unavailable in this browser.";
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    utterance.rate = 0.95;
    const voices = window.speechSynthesis.getVoices();
    utterance.voice =
      voices.find((v) => v.localService && v.lang.startsWith("en")) ??
      voices.find((v) => v.lang.startsWith("en")) ??
      null;
    const spans = wordsRef.current;
    const clear = () => spans.forEach((span) => span.classList.remove("speaking-word"));
    utterance.onstart = () => {
      setSpeaking(true);
      if (statusRef.current) statusRef.current.textContent = "Speaking…";
    };
    utterance.onboundary = (event) => {
      clear();
      let position = 0;
      for (const span of spans) {
        const length = span.textContent?.length ?? 0;
        if (position <= event.charIndex && event.charIndex < position + length) {
          span.classList.add("speaking-word");
          break;
        }
        position += length;
      }
    };
    utterance.onend = () => {
      clear();
      setSpeaking(false);
      if (statusRef.current) statusRef.current.textContent = "Your turn. Record your answer below.";
    };
    utterance.onerror = () => {
      clear();
      setSpeaking(false);
      if (statusRef.current)
        statusRef.current.textContent = "Select Read aloud to enable speech, or read the question above.";
    };
    window.speechSynthesis.speak(utterance);
  };

  useEffect(() => {
    let should = autoplay;
    try {
      const key = `interview-spoken-${turnId}`;
      if (sessionStorage.getItem(key)) should = false;
      else if (should) sessionStorage.setItem(key, "1");
    } catch {
      /* storage unavailable */
    }
    if (should) {
      const timer = setTimeout(speak, 250);
      return () => {
        clearTimeout(timer);
        if ("speechSynthesis" in window) window.speechSynthesis.cancel();
      };
    }
    return () => {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turnId, text]);

  useEffect(() => stop, [turnId]);
  return { speak, stop, speaking, supported, wordsRef, statusRef };
}
