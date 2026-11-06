// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InterviewSetup } from "./InterviewSetup";

vi.mock("@mediapipe/tasks-vision", () => ({ FaceDetector: {}, FilesetResolver: {} }));

function fakeDisplayStream(settings: object) {
  const track = { readyState: "live", getSettings: () => settings, stop: vi.fn(), addEventListener: vi.fn() };
  return { getVideoTracks: () => [track], getTracks: () => [track] };
}

function mockMediaDevices(getDisplayMedia: ReturnType<typeof vi.fn>, supported: object) {
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getDisplayMedia, getSupportedConstraints: () => supported },
    configurable: true,
  });
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); });

describe("screen sharing check", () => {
  it("passes a Firefox entire-screen share the browser cannot describe afterwards", async () => {
    const getDisplayMedia = vi.fn(async () => fakeDisplayStream({ width: 1920, height: 1080 }));
    mockMediaDevices(getDisplayMedia, { mediaSource: true });
    render(<InterviewSetup token="test-token" onReady={() => {}} onContinue={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Share entire screen" }));
    expect(await screen.findByText("Entire screen shared")).toBeDefined();
    expect(getDisplayMedia).toHaveBeenCalledWith({ video: { mediaSource: "screen" }, audio: false });
  });
  it("keeps verifying Chromium shares against reported metadata", async () => {
    const getDisplayMedia = vi.fn(async () => fakeDisplayStream({ displaySurface: "monitor" }));
    mockMediaDevices(getDisplayMedia, { displaySurface: true });
    render(<InterviewSetup token="test-token" onReady={() => {}} onContinue={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Share entire screen" }));
    expect(await screen.findByText("Entire screen shared")).toBeDefined();
    expect(getDisplayMedia).toHaveBeenCalledWith({ video: true, audio: false });
  });
  it("rejects an undescribed share from an unrestricted picker", async () => {
    const getDisplayMedia = vi.fn(async () => fakeDisplayStream({ width: 1920, height: 1080 }));
    mockMediaDevices(getDisplayMedia, {});
    render(<InterviewSetup token="test-token" onReady={() => {}} onContinue={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Share entire screen" }));
    expect(await screen.findByText(/did not identify/)).toBeDefined();
    expect(screen.queryByText("Entire screen shared")).toBeNull();
  });
});
