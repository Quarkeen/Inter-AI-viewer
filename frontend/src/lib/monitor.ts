import { useCallback, useLayoutEffect, useRef } from "react";
import { api, ApiError } from "./api";
import type { WarningSnapshot } from "./warningSnapshot";

/** Browser-behavior observations for the interview room.
 *
 * The `at` stamp is client-side; the server adds its own receipt time. These are
 * untrusted review signals for HR, never automatic verdicts: a visibility change
 * only shows the interview lost focus, not what the candidate did elsewhere.
 */

export interface MonitorEvent {
  id?: string;
  snapshot?: string;
  snapshot_at?: string;
  kind: string;
  detail: Record<string, unknown>;
  at: string;
}

/** How long a fullscreen or presence violation warns before it pauses the interview. */
export const WARN_GRACE_MS = 30_000;

/** Graduated stage of a continuous violation: warn first, pause only past the grace. */
export function violationStage(sinceMs: number | null, nowMs: number, graceMs = WARN_GRACE_MS): "ok" | "warn" | "pause" {
  if (sinceMs == null) return "ok";
  return nowMs - sinceMs >= graceMs ? "pause" : "warn";
}

/** Observe tab visibility, window focus, fullscreen and clipboard use. Returns a stop function. */
export function watchBrowserEvents(onEvent: (event: MonitorEvent) => void): () => void {
  const emit = (kind: string, detail: Record<string, unknown>) => onEvent({ kind, detail, at: new Date().toISOString() });
  const onVisibility = () => emit("browser.visibility", { hidden: document.hidden });
  const onBlur = () => emit("browser.focus", { focused: false });
  const onFocus = () => emit("browser.focus", { focused: true });
  const onFullscreen = () => emit("browser.fullscreen", { fullscreen: document.fullscreenElement != null });
  const onCopy = () => emit("browser.clipboard", { action: "copy" });
  const onCut = () => emit("browser.clipboard", { action: "cut" });
  const onPaste = () => emit("browser.clipboard", { action: "paste" });
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("blur", onBlur);
  window.addEventListener("focus", onFocus);
  document.addEventListener("fullscreenchange", onFullscreen);
  document.addEventListener("copy", onCopy);
  document.addEventListener("cut", onCut);
  document.addEventListener("paste", onPaste);
  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("focus", onFocus);
    document.removeEventListener("fullscreenchange", onFullscreen);
    document.removeEventListener("copy", onCopy);
    document.removeEventListener("cut", onCut);
    document.removeEventListener("paste", onPaste);
  };
}

/** Failed batches retain stable IDs. Warning evidence is captured at the event,
 * not later during a retry. Low-priority telemetry is capped independently. */
export function useMonitorQueue(token: string, sessionId: string | null, options?: {
  capture?: () => WarningSnapshot | null;
  activeWarnings?: Record<string, string>;
  onWarningsSynced?: () => void;
}): (event: MonitorEvent) => void {
  const sink = useRef<(event: MonitorEvent) => void>(() => {});
  const settings = useRef(options); settings.current = options;
  useLayoutEffect(() => {
    if (!sessionId) { sink.current = () => {}; return; }
    const queue: MonitorEvent[] = [];
    const active = new Map(Object.entries(settings.current?.activeWarnings ?? {}));
    let sending = false;
    const capTelemetry = () => {
      let excess = queue.filter(item => !item.kind.startsWith("warn.")).length - 200;
      for (let index = 0; index < queue.length && excess > 0;) {
        if (!queue[index].kind.startsWith("warn.")) { queue.splice(index, 1); excess--; } else index++;
      }
    };
    const flush = async () => {
      if (sending || queue.length === 0) return;
      sending = true;
      let count = 0, size = 0;
      while (count < queue.length && count < 200) {
        const next = JSON.stringify(queue[count]).length + 1;
        if (count > 0 && size + next > 500_000) break;
        size += next; count++;
      }
      const batch = queue.splice(0, count);
      let success = false;
      try {
        await api.reportEvents(token, batch);
        success = true;
        if (batch.some(item => item.kind.startsWith("warn."))) settings.current?.onWarningsSynced?.();
      } catch (error) {
        // A damaged image must not permanently block warning metadata or policy.
        if (error instanceof ApiError && (error.status === 400 || error.status === 413)) {
          batch.forEach(item => { delete item.snapshot; delete item.snapshot_at; });
        }
        queue.unshift(...batch); capTelemetry();
      } finally {
        sending = false;
        if (success && queue.length) void flush();
      }
    };
    sink.current = original => {
      let event: MonitorEvent = { ...original, id: original.id ?? crypto.randomUUID(), detail: { ...original.detail } };
      const signal = String(event.detail.signal ?? "");
      if (event.kind === "warn.started") {
        const identity = active.get(signal) ?? crypto.randomUUID();
        active.set(signal, identity); event.detail.warning_id = identity;
        const frame = settings.current?.capture?.();
        if (frame) event = { ...event, snapshot: frame.data, snapshot_at: frame.at };
      } else if (event.kind === "warn.cleared") {
        const identity = active.get(signal);
        if (!identity) return;
        event.detail.warning_id = identity; active.delete(signal);
      }
      queue.push(event); capTelemetry();
      if (event.kind.startsWith("warn.")) void flush();
    };
    const timer = setInterval(() => void flush(), 5000);
    const onHide = () => void flush();
    window.addEventListener("pagehide", onHide);
    return () => {
      clearInterval(timer); window.removeEventListener("pagehide", onHide);
      sink.current = () => {}; void flush();
    };
  }, [token, sessionId]);
  return useCallback((event: MonitorEvent) => sink.current(event), []);
}
