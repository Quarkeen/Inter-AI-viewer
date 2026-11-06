// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { violationStage, watchBrowserEvents, type MonitorEvent } from "./monitor";

describe("violationStage", () => {
  it("warns first and pauses only past the grace period", () => {
    expect(violationStage(null, 1000)).toBe("ok");
    expect(violationStage(1000, 1000)).toBe("warn");
    expect(violationStage(1000, 30999)).toBe("warn");
    expect(violationStage(1000, 31000)).toBe("pause");
    expect(violationStage(1000, 90000)).toBe("pause");
  });
});

describe("watchBrowserEvents", () => {
  it("reports visibility, focus, fullscreen and clipboard changes with timestamps", () => {
    const seen: MonitorEvent[] = [];
    const stop = watchBrowserEvents(event => seen.push(event));
    try {
      Object.defineProperty(document, "hidden", { value: true, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("blur"));
      window.dispatchEvent(new Event("focus"));
      Object.defineProperty(document, "fullscreenElement", { value: document.documentElement, configurable: true });
      document.dispatchEvent(new Event("fullscreenchange"));
      document.dispatchEvent(new Event("copy"));
      document.dispatchEvent(new Event("paste"));
    } finally {
      stop();
      Object.defineProperty(document, "hidden", { value: false, configurable: true });
      Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true });
    }
    expect(seen.map(event => [event.kind, event.detail])).toEqual([
      ["browser.visibility", { hidden: true }],
      ["browser.focus", { focused: false }],
      ["browser.focus", { focused: true }],
      ["browser.fullscreen", { fullscreen: true }],
      ["browser.clipboard", { action: "copy" }],
      ["browser.clipboard", { action: "paste" }],
    ]);
    for (const event of seen) expect(Number.isNaN(Date.parse(event.at))).toBe(false);
  });
  it("stops reporting once the watcher is stopped", () => {
    const seen: MonitorEvent[] = [];
    const stop = watchBrowserEvents(event => seen.push(event));
    stop();
    window.dispatchEvent(new Event("blur"));
    document.dispatchEvent(new Event("visibilitychange"));
    expect(seen).toEqual([]);
  });
});
