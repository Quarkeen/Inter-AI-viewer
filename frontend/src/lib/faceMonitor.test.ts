import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FaceDetector, type FaceDetector as FaceDetectorType } from "@mediapipe/tasks-vision";
import { faceCountEvent, sampleFaceCount, watchFaceCount } from "./faceMonitor";

vi.mock("@mediapipe/tasks-vision", () => ({
  FaceDetector: { createFromOptions: vi.fn() },
  FilesetResolver: { forVisionTasks: vi.fn(async () => ({})) },
}));

function detectorWith(faces: number) {
  return { detectForVideo: vi.fn(() => ({ detections: new Array(faces).fill({}) })), close: vi.fn() };
}

describe("faceCountEvent", () => {
  it("emits the first reading as a baseline", () => {
    expect(faceCountEvent(null, 1)).toEqual({ count: 1 });
  });
  it("emits only when the count changes", () => {
    expect(faceCountEvent(1, 1)).toBeNull();
    expect(faceCountEvent(1, 0)).toEqual({ count: 0 });
    expect(faceCountEvent(0, 2)).toEqual({ count: 2 });
  });
  it("ignores failed readings without resetting the baseline", () => {
    expect(faceCountEvent(1, null)).toBeNull();
    expect(faceCountEvent(null, null)).toBeNull();
  });
});

describe("sampleFaceCount", () => {
  it("counts detections on a ready video frame", () => {
    const detector = detectorWith(2);
    expect(sampleFaceCount(detector, { readyState: 4 } as HTMLVideoElement)).toBe(2);
    expect(detector.detectForVideo).toHaveBeenCalledOnce();
  });
  it("skips sampling while the video is not ready", () => {
    const detector = detectorWith(1);
    expect(sampleFaceCount(detector, { readyState: 1 } as HTMLVideoElement)).toBeNull();
    expect(detector.detectForVideo).not.toHaveBeenCalled();
  });
  it("reports a failed detection as unknown", () => {
    const detector = { detectForVideo: vi.fn(() => { throw new Error("no frame"); }), close: vi.fn() };
    expect(sampleFaceCount(detector, { readyState: 4 } as HTMLVideoElement)).toBeNull();
  });
});

describe("watchFaceCount", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("samples the face count on an interval until stopped", async () => {
    vi.mocked(FaceDetector.createFromOptions).mockResolvedValue(detectorWith(2) as unknown as FaceDetectorType);
    const onCount = vi.fn();
    const stop = watchFaceCount({ readyState: 4 } as HTMLVideoElement, onCount, 1000);
    await vi.advanceTimersByTimeAsync(10);
    expect(FaceDetector.createFromOptions).toHaveBeenCalledOnce();
    expect(onCount).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(onCount).toHaveBeenCalledTimes(1);
    expect(onCount).toHaveBeenCalledWith(2);
    stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(onCount).toHaveBeenCalledTimes(1);
  });
  it("stays silent when the detector cannot load", async () => {
    vi.mocked(FaceDetector.createFromOptions).mockRejectedValue(new Error("no wasm"));
    const onCount = vi.fn();
    const stop = watchFaceCount({ readyState: 4 } as HTMLVideoElement, onCount, 100);
    await vi.advanceTimersByTimeAsync(500);
    expect(onCount).not.toHaveBeenCalled();
    stop();
  });
});
