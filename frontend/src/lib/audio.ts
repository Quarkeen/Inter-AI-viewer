/** Record microphone audio and encode it as 16 kHz mono WAV, like the original app. */
import { availableMicrophones, openInterviewMedia } from './microphones';

export async function listMicrophones(): Promise<MediaDeviceInfo[]> {
  return availableMicrophones();
}

export async function recordAnswerMic(deviceId?: string): Promise<MediaRecorder> {
  const stream = await openInterviewMedia(deviceId);
  try { return new MediaRecorder(stream); }
  catch (error) { stream.getTracks().forEach(track => track.stop()); throw error; }
}

/** Call onLevel with the live RMS input level (0 = silence) until stopped. */
export function watchMicLevel(stream: MediaStream, onLevel: (level: number) => void): () => void {
  const context = new AudioContext();
  void context.resume().catch(() => {});
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  const data = new Float32Array(analyser.fftSize);
  let alive = true;
  const tick = () => {
    if (!alive) return;
    analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    onLevel(Math.sqrt(sum / data.length));
    requestAnimationFrame(tick);
  };
  tick();
  return () => {
    alive = false;
    source.disconnect();
    analyser.disconnect();
    void context.close();
  };
}

export function collectRecording(recorder: MediaRecorder): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const chunks: BlobPart[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onerror = () => reject(new Error("Recording failed. Please try again."));
    recorder.onstop = () => {
      recorder.stream.getTracks().forEach((track) => track.stop());
      resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
    };
    recorder.start();
  });
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** Decode any recorded audio and resample it to 16 kHz mono WAV. */
export async function toWav16k(blob: Blob): Promise<Blob> {
  const context = new AudioContext({ sampleRate: 16000 });
  try {
    const raw = await blob.arrayBuffer();
    const decoded = await context.decodeAudioData(raw);
    const channel = decoded.getChannelData(0);
    return encodeWav(channel, 16000);
  } finally {
    await context.close();
  }
}
