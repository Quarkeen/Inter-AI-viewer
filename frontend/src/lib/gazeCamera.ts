import { GAZE_MAX_GAP_MS, gazeFeatures, type GazeReading, type Landmark } from './gaze';

/** Idle video suspends sampling; only failed/pending worker requests trigger
 * a restart. The existing camera and calibration are retained. */
export function watchGaze(video: HTMLVideoElement, onReading: (value: GazeReading | null) => void, onError: () => void): () => void {
  let worker: Worker | null = null;
  let alive = true, ready = false, pending = false, lastVideoTime = -1;
  let generation = 0, requestAt = 0, frameAt = 0, retryDelay = 1000;
  let timer: ReturnType<typeof setInterval> | undefined;
  let startup: ReturnType<typeof setTimeout> | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const releaseWorker = () => {
    generation++; clearTimeout(startup); worker?.terminate(); worker = null;
    ready = false; pending = false; lastVideoTime = -1;
  };
  const stop = () => { alive = false; clearInterval(timer); clearTimeout(retry); releaseWorker(); };
  const recover = () => {
    if (!alive || retry != null) return;
    releaseWorker(); onReading(null); onError();
    retry = setTimeout(() => { retry = undefined; if (alive) start(); }, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10_000);
  };
  const start = () => {
    if (!alive) return;
    try {
      const instance = new Worker('/preflight/gaze-worker.js'); worker = instance;
      const current = ++generation;
      startup = setTimeout(recover, 25_000);
      instance.onerror = () => { if (alive && generation === current) recover(); };
      instance.onmessage = (event: MessageEvent<{ kind: string; faces?: Landmark[][]; aspect?: number; at?: number }>) => {
        if (!alive || generation !== current) return;
        if (event.data.kind === 'ready') { ready = true; clearTimeout(startup); }
        else if (event.data.kind === 'reading') {
          pending = false; retryDelay = 1000;
          const stale = performance.now() - (event.data.at ?? requestAt) > GAZE_MAX_GAP_MS;
          onReading(stale || document.hidden || video.paused ? null : gazeFeatures(event.data.faces ?? [], event.data.aspect));
        } else recover();
      };
      instance.postMessage({ kind: 'init' });
    } catch { recover(); }
  };
  if (!window.Worker || !window.createImageBitmap) { onReading(null); onError(); return stop; }
  start();
  timer = setInterval(() => {
    if (!alive || !ready) return;
    // No response is expected while the browser has no video frames to send.
    if (document.hidden || video.readyState < 2 || video.paused) { onReading(null); return; }
    if (pending) { if (performance.now() - requestAt > 5000) recover(); return; }
    if (video.currentTime === lastVideoTime) {
      if (performance.now() - frameAt > GAZE_MAX_GAP_MS) onReading(null);
      return;
    }
    lastVideoTime = video.currentTime; frameAt = performance.now(); pending = true; requestAt = frameAt;
    const current = generation, instance = worker!;
    void createImageBitmap(video).then(frame => {
      if (!alive || generation !== current) { frame.close(); return; }
      if (document.hidden || video.paused) { frame.close(); pending = false; onReading(null); return; }
      instance.postMessage({ kind: 'frame', frame, at: requestAt }, [frame]);
    }).catch(() => { if (alive && generation === current) recover(); });
  }, 125);
  return stop;
}
export function cameraDevice(video: HTMLVideoElement): string {
  return (video.srcObject as MediaStream | null)?.getVideoTracks?.()[0]?.getSettings?.().deviceId ?? '';
}
