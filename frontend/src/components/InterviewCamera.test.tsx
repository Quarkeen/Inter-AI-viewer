// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FaceDetector } from "@mediapipe/tasks-vision";
import { InterviewCamera } from "./InterviewCamera";
import type { MonitorEvent } from "../lib/monitor";

vi.mock("@mediapipe/tasks-vision", () => ({
  FaceDetector: { createFromOptions: vi.fn() },
  FilesetResolver: { forVisionTasks: vi.fn(async () => ({})) },
}));

function trackStub() {
  const listeners: Record<string, () => void> = {};
  const track = {
    stop: vi.fn(),
    addEventListener: vi.fn((name: string, callback: () => void) => {
      listeners[name] = callback;
    }),
  };
  return { track, listeners };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.HTMLMediaElement.prototype.play = vi.fn(async () => {});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("InterviewCamera", () => {
  it("reports camera start and face-count changes, and releases the camera on unmount", async () => {
    const { track } = trackStub();
    const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn(async () => stream) },
      configurable: true,
    });
    const detectForVideo = vi
      .fn()
      .mockReturnValueOnce({ detections: [{}] })
      .mockReturnValue({ detections: [] });
    vi.mocked(FaceDetector.createFromOptions).mockResolvedValue({ detectForVideo, close: vi.fn() } as never);
    const seen: MonitorEvent[] = [];
    const { container, unmount } = render(<InterviewCamera sampleMs={30} onEvent={event => seen.push(event)} />);
    Object.defineProperty(container.querySelector("video")!, "readyState", { value: 4, configurable: true });
    await waitFor(() => expect(seen).toContainEqual(expect.objectContaining({ kind: "camera.state", detail: { state: "started" } })));
    await waitFor(() => expect(seen).toContainEqual(expect.objectContaining({ kind: "face.count", detail: { count: 1 } })));
    await waitFor(() => expect(seen).toContainEqual(expect.objectContaining({ kind: "face.count", detail: { count: 0 } })));
    unmount();
    expect(track.stop).toHaveBeenCalled();
  });
  it("reports when the camera is unavailable and lets the interview continue", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn(async () => { throw new DOMException("denied", "NotAllowedError"); }) },
      configurable: true,
    });
    const seen: MonitorEvent[] = [];
    render(<InterviewCamera onEvent={event => seen.push(event)} />);
    expect(await screen.findByText(/Camera unavailable/)).toBeDefined();
    await waitFor(() => expect(seen).toContainEqual(expect.objectContaining({ kind: "camera.state", detail: { state: "unavailable" } })));
  });
  it("retries acquisition when the candidate asks", async () => {
    const getUserMedia = vi.fn(async () => {
      throw new DOMException("denied", "NotAllowedError");
    });
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    render(<InterviewCamera onEvent={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry camera" }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
  });
  it("reports when the camera disconnects mid-interview", async () => {
    const { track, listeners } = trackStub();
    const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn(async () => stream) },
      configurable: true,
    });
    vi.mocked(FaceDetector.createFromOptions).mockResolvedValue({ detectForVideo: vi.fn(() => ({ detections: [{}] })), close: vi.fn() } as never);
    const seen: MonitorEvent[] = [];
    render(<InterviewCamera sampleMs={30} onEvent={event => seen.push(event)} />);
    await screen.findByText(/Monitoring your presence/);
    await act(async () => {
      listeners["ended"]();
    });
    expect(await screen.findByText(/Camera disconnected/)).toBeDefined();
    expect(seen).toContainEqual(expect.objectContaining({ kind: "camera.state", detail: { state: "ended" } }));
  });
});
