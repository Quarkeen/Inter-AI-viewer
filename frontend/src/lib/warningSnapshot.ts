export interface WarningSnapshot { data: string; at: string }

/** Capture the current camera frame only; never substitute a cached old image. */
export function captureWarningSnapshot(video: HTMLVideoElement | null): WarningSnapshot | null {
  if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return null;
  const stream = video.srcObject as MediaStream | null;
  if (!stream?.getVideoTracks().some(track => track.readyState === "live")) return null;
  try {
    const scale = Math.min(1, 320 / video.videoWidth, 240 / video.videoHeight);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext("2d");
    if (!context) return null;
    const at = new Date().toISOString();
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const data = canvas.toDataURL("image/jpeg", 0.7);
    return data.startsWith("data:image/jpeg;base64,") && data.length <= 170_000 ? { data, at } : null;
  } catch { return null; }
}
