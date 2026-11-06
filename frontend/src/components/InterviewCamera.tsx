import { useEffect, useRef, useState } from "react";
import { Button, Card } from "./ui";
import { faceCountEvent, watchFaceCount } from "../lib/faceMonitor";
import type { MonitorEvent } from "../lib/monitor";
import { captureWarningSnapshot, type WarningSnapshot } from '../lib/warningSnapshot';

/** Small interview-room camera with on-device face presence sampling.
 * Presence changes are reported as review signals; a missing camera never blocks. */
export function InterviewCamera({ onEvent, sampleMs = 2000, snapshotRef, onVideoReady }: { onVideoReady?: (video: HTMLVideoElement | null) => void; onEvent: (event: MonitorEvent) => void; sampleMs?: number; snapshotRef?: { current: (() => WarningSnapshot | null) | null } }) {
  const video = useRef<HTMLVideoElement>(null);
  const callback = useRef(onEvent);
  callback.current = onEvent;
  const videoCallback = useRef(onVideoReady); videoCallback.current = onVideoReady;
  const [cameraState, setCameraState] = useState<"starting" | "live" | "unavailable" | "ended">("starting");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!snapshotRef) return;
    snapshotRef.current = () => captureWarningSnapshot(video.current);
    return () => { snapshotRef.current = null; };
  }, [snapshotRef]);
  const retry = () => {
    setCameraState("starting");
    setAttempt(count => count + 1);
  };

  useEffect(() => {
    let alive = true;
    let stream: MediaStream | null = null;
    let stopSampling = () => {};
    let previous: number | null = null;
    const report = (kind: string, detail: Record<string, unknown>) =>
      callback.current({ kind, detail, at: new Date().toISOString() });
    void (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera is not supported here.");
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
        const element = video.current;
        if (!alive || !element) {
          stream.getTracks().forEach(track => track.stop());
          return;
        }
        element.srcObject = stream;
        await element.play();
        if (!alive) return;
        stream.getTracks().forEach(track =>
          track.addEventListener("ended", () => {
            if (!alive) return;
            stopSampling();
            videoCallback.current?.(null);
            setCameraState("ended");
            report("camera.state", { state: "ended" });
          }),
        );
        setCameraState("live");
        videoCallback.current?.(element);
        report("camera.state", { state: "started" });
        stopSampling = watchFaceCount(
          element,
          count => {
            const detail = faceCountEvent(previous, count);
            if (count != null) previous = count;
            if (detail && alive) report("face.count", detail);
          },
          sampleMs,
        );
      } catch {
        if (alive) {
          setCameraState("unavailable");
          report("camera.state", { state: "unavailable" });
        }
      }
    })();
    return () => {
      alive = false;
      stopSampling();
      videoCallback.current?.(null);
      stream?.getTracks().forEach(track => track.stop());
    };
    // Re-acquire only on explicit retry: restarting would drop the presence baseline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  return (
    <Card className="py-3">
      <p className="text-xs text-ink-500">Camera monitor</p>
      <div className="mt-2 overflow-hidden rounded-lg bg-slate-900">
        <video ref={video} muted autoPlay playsInline className="aspect-video w-full object-cover" aria-label="Interview camera monitor" />
      </div>
      <p className="mt-2 text-xs text-ink-500" role="status">
        {cameraState === "live" && "Monitoring your presence."}
        {cameraState === "starting" && "Starting camera…"}
        {cameraState === "unavailable" && "Camera unavailable — the interview continues and this is noted for review."}
        {cameraState === "ended" && "Camera disconnected — this interruption is noted for review."}
      </p>
      {(cameraState === "unavailable" || cameraState === "ended") && (
        <div className="mt-2">
          <Button variant="secondary" onClick={retry}>
            Retry camera
          </Button>
        </div>
      )}
    </Card>
  );
}
