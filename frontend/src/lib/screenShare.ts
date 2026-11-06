/** Entire-screen capture checks shared by the setup page.
 *
 * Two enforcement paths, never silent approval:
 * - Chromium reports `displaySurface` after capture; the grant is verified post-hoc.
 * - Firefox honors the `mediaSource: "screen"` request constraint by offering only
 *   entire screens in its picker, but may not describe the grant afterwards. When that
 *   constraint was applied, the picker itself enforced entire-screen capture.
 * Unrestricted requests without identifying metadata are always rejected, and explicit
 * window/tab metadata always wins over the request. Scope is never inferred from
 * labels or dimensions.
 */

/** Request constraints for an entire-screen-only capture. Pass `supported` in tests. */
export function entireScreenConstraints(supported?: Record<string, boolean>): { video: MediaTrackConstraints | true; pickerRestricted: boolean } {
  const caps = (supported ?? globalThis.navigator?.mediaDevices?.getSupportedConstraints?.() ?? {}) as Record<string, boolean | undefined>;
  if (caps.mediaSource) return { video: { mediaSource: "screen" } as MediaTrackConstraints, pickerRestricted: true };
  return { video: true, pickerRestricted: false };
}

export function requireEntireScreen(stream: MediaStream, pickerRestricted = false): "monitor" {
  const track = stream.getVideoTracks()[0];
  const reject = (message: string): never => {
    stream.getTracks().forEach(item => item.stop());
    throw new Error(message);
  };
  if (!track || track.readyState !== "live") return reject("Screen sharing ended before it could be checked. Please share your screen again.");
  const settings = track.getSettings() as MediaTrackSettings & { mediaSource?: string };
  const surface = settings.displaySurface;
  if (surface === "monitor") return "monitor";
  if (surface) return reject("Choose Entire screen, rather than a tab or window.");
  // Firefox/Zen use the Mozilla-specific source field instead of displaySurface.
  if (settings.mediaSource === "screen") return "monitor";
  if (["window", "browser", "application"].includes(settings.mediaSource ?? "")) {
    return reject("Choose Entire screen, rather than a tab or window.");
  }
  if (pickerRestricted) return "monitor";
  return reject("Your browser did not identify the shared screen type. Try an updated browser that reports full-screen capture, such as Chrome or Chromium, and select Entire screen.");
}
