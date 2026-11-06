import { describe, expect, it, vi } from "vitest";
import { entireScreenConstraints, requireEntireScreen } from "./screenShare";

function capture(settings: object, live = true) {
  const stop = vi.fn();
  const track = { readyState: live ? "live" : "ended", getSettings: () => settings, stop };
  const stream = { getVideoTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream;
  return { stream, stop };
}

describe("whole-screen capture compatibility", () => {
  it("accepts Chromium monitor metadata", () => {
    const { stream, stop } = capture({ displaySurface: "monitor" });
    expect(requireEntireScreen(stream)).toBe("monitor");
    expect(stop).not.toHaveBeenCalled();
  });
  it("accepts Firefox/Zen explicit screen metadata", () => {
    const { stream, stop } = capture({ mediaSource: "screen" });
    expect(requireEntireScreen(stream)).toBe("monitor");
    expect(stop).not.toHaveBeenCalled();
  });
  it.each([{ displaySurface: "window" }, { displaySurface: "browser" }, { mediaSource: "window" }, { mediaSource: "browser" }, { displaySurface: "window", mediaSource: "screen" }])("rejects window/tab capture and stops it: %j", settings => {
    const { stream, stop } = capture(settings);
    expect(() => requireEntireScreen(stream)).toThrow("Choose Entire screen");
    expect(stop).toHaveBeenCalled();
  });
  it("does not silently approve a source when metadata is unavailable", () => {
    const { stream, stop } = capture({ width: 1920, height: 1080 });
    expect(() => requireEntireScreen(stream)).toThrow("did not identify");
    expect(stop).toHaveBeenCalled();
  });
  it("accepts a picker-restricted share the browser cannot describe afterwards", () => {
    const { stream, stop } = capture({ width: 1920, height: 1080 });
    expect(requireEntireScreen(stream, true)).toBe("monitor");
    expect(stop).not.toHaveBeenCalled();
  });
  it("rejects explicit window metadata even from a restricted picker", () => {
    const { stream, stop } = capture({ displaySurface: "window" });
    expect(() => requireEntireScreen(stream, true)).toThrow("Choose Entire screen");
    expect(stop).toHaveBeenCalled();
  });
  it("rejects ended capture", () => {
    const { stream } = capture({ displaySurface: "monitor" }, false);
    expect(() => requireEntireScreen(stream)).toThrow("ended");
  });
});

describe("entire-screen request constraints", () => {
  it("restricts the Firefox picker to screens when mediaSource is supported", () => {
    expect(entireScreenConstraints({ mediaSource: true })).toEqual({ video: { mediaSource: "screen" }, pickerRestricted: true });
  });
  it("leaves the request unrestricted when no picker constraint is supported", () => {
    expect(entireScreenConstraints({})).toEqual({ video: true, pickerRestricted: false });
  });
  it("keeps Chromium on post-hoc verification", () => {
    expect(entireScreenConstraints({ displaySurface: true })).toEqual({ video: true, pickerRestricted: false });
  });
});
