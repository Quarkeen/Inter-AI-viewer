/** Periodic face presence/count sampling for the interview room.
 *
 * Counts come from the on-device MediaPipe face detector; only changes are worth
 * reporting, so callers translate readings with `faceCountEvent` before sending.
 * A `null` reading means the check itself failed (unready video, detector error),
 * which is distinct from a concrete zero-face reading.
 */
import { FaceDetector, FilesetResolver } from "@mediapipe/tasks-vision";

type Detector = Pick<FaceDetector, "detectForVideo">;

/** Detail payload when a reading differs from the previous one, else null. The first
 * concrete reading is the baseline. Failed readings never emit and never reset it. */
export function faceCountEvent(previous: number | null, current: number | null): { count: number } | null {
  if (current == null || current === previous) return null;
  return { count: current };
}

/** One synchronous face-count sample. Never throws. */
export function sampleFaceCount(detector: Detector, video: HTMLVideoElement): number | null {
  if (video.readyState < 2) return null;
  try {
    return detector.detectForVideo(video, performance.now()).detections.length;
  } catch {
    return null;
  }
}

/** Sample the face count every `intervalMs` until the returned stop function runs.
 * Stays silent when the detector cannot load. */
export function watchFaceCount(video: HTMLVideoElement, onCount: (count: number | null) => void, intervalMs = 2000): () => void {
  let alive = true;
  let timer: ReturnType<typeof setInterval> | null = null;
  let detector: FaceDetector | null = null;
  void (async () => {
    try {
      const engine = await FaceDetector.createFromOptions(await FilesetResolver.forVisionTasks("/preflight/wasm"), {
        baseOptions: { modelAssetPath: "/preflight/blaze_face_short_range.tflite", delegate: "CPU" },
        runningMode: "VIDEO",
        minDetectionConfidence: 0.6,
      });
      if (!alive) {
        engine.close();
        return;
      }
      detector = engine;
      timer = setInterval(() => {
        if (alive && detector) onCount(sampleFaceCount(detector, video));
      }, intervalMs);
    } catch {
      /* detector unavailable: the interview continues without face sampling */
    }
  })();
  return () => {
    alive = false;
    if (timer) clearInterval(timer);
    detector?.close();
    detector = null;
  };
}
