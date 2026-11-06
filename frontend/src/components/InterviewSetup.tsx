import { useEffect, useRef, useState } from "react";
import { FaceDetector, FilesetResolver } from "@mediapipe/tasks-vision";
import { api, ApiError } from "../lib/api";
import { entireScreenConstraints, requireEntireScreen } from "../lib/screenShare";
import { collectRecording, toWav16k, watchMicLevel } from "../lib/audio";
import { ArrowRight, Camera, CheckCircle2, Monitor, Volume2 } from "lucide-react";
import { Alert, Button, Card, Field, Select } from "./ui";
import { availableMicrophones, isLoopbackInput, microphoneError, microphoneLabel, openInterviewMedia } from '../lib/microphones';

// Setup checks only: no identity matching, gaze grading or screen recording.
export function InterviewSetup({ token, onReady, onContinue, active = true, onVideoReady }: { onVideoReady?: (video: HTMLVideoElement | null) => void; active?: boolean; token: string; onReady: (receipt: string | null, mic: string) => void; onContinue: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const streams = useRef<MediaStream[]>([]);
  const detector = useRef<FaceDetector | null>(null);
  const micStream = useRef<MediaStream | null>(null);
  const displayStream = useRef<MediaStream | null>(null);
  const micChoice = useRef('');
  const recorder = useRef<MediaRecorder | null>(null);
  const stopLevel = useRef<(() => void) | null>(null);
  const alive = useRef(true);
  const generation = useRef(0);
  const expiry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callback = useRef(onReady); callback.current = onReady;
  const videoCallback = useRef(onVideoReady); videoCallback.current = onVideoReady;
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [micId, setMicId] = useState("");
  const [micConnected, setMicConnected] = useState(false);
  const [micName, setMicName] = useState('');
  const [refreshingMics, setRefreshingMics] = useState(false);
  const [faces, setFaces] = useState<number | null>(null);
  const [light, setLight] = useState(false);
  const [devices, setDevices] = useState(false);
  const [screen, setScreen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [sample, setSample] = useState<Blob | null>(null);
  const [sampleUrl, setSampleUrl] = useState("");
  const [playbackPlayed, setPlaybackPlayed] = useState(false);
  const [heardRecording, setHeardRecording] = useState(false);
  const [speakerPlayed, setSpeakerPlayed] = useState(false);
  const [speakerConfirmed, setSpeakerConfirmed] = useState(false);
  const [rules, setRules] = useState(false);
  const [level, setLevel] = useState(0);
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState("");
  const cameraGood = devices && faces === 1 && light;

  function invalidate() {
    generation.current++;
    callback.current(null, micChoice.current);
    setVerified(false);
    if (expiry.current) clearTimeout(expiry.current);
  }
  function stopMicrophoneLevel() {
    stopLevel.current?.(); stopLevel.current = null;
  }
  function stopDevices() {
    videoCallback.current?.(null);
    stopMicrophoneLevel();
    streams.current.forEach(stream => stream.getTracks().forEach(track => track.stop()));
    streams.current = []; micStream.current = null; displayStream.current = null;
    if (alive.current) { setMicConnected(false); setMicName(''); setLevel(0); }
  }
  async function refreshMicrophones() {
    setRefreshingMics(true);
    try {
      const available = await availableMicrophones();
      if (alive.current) setMics(available);
    } catch { if (alive.current) setError('Microphones could not be listed. Check browser permissions and refresh the device list.'); }
    finally { if (alive.current) setRefreshingMics(false); }
  }
  useEffect(() => {
    void refreshMicrophones();
    const update = () => void refreshMicrophones();
    navigator.mediaDevices?.addEventListener?.('devicechange', update);
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', update);
  }, []);

  function attachMicrophone(stream: MediaStream) {
    const track = stream.getAudioTracks()[0];
    const actual = track.getSettings().deviceId || micChoice.current;
    const playback = isLoopbackInput(track.label);
    // Do not pin automatic reconnects to a playback monitor selected by the browser.
    micChoice.current = playback ? '' : actual; setMicId(actual); setMicName(track.label || 'Selected microphone');
    micStream.current = stream; setMicConnected(!playback);
    stopLevel.current?.();
    stopLevel.current = playback ? null : watchMicLevel(stream, value => { if (alive.current) setLevel(value); });
    if (playback) {
      setLevel(0);
      setError('The browser selected speaker playback instead of your microphone. Select your microphone below or reconnect after changing the browser microphone permission. If it is missing, clear this site’s microphone permission and allow access again.');
    }
    track.addEventListener('ended', () => {
      if (alive.current && micStream.current === stream) {
        setMicConnected(false); setLevel(0); invalidate();
        setSample(null); setSampleUrl(''); setHeardRecording(false); setPlaybackPlayed(false);
        setError('The microphone disconnected. Select a connected microphone and retry.');
      }
    });
  }

  async function changeMicrophone(next: string) {
    invalidate(); micChoice.current = next; setMicId(next); setError('');
    setSample(null); setSampleUrl(''); setHeardRecording(false); setPlaybackPlayed(false);
    if (!devices) return;
    setBusy(true); setMicConnected(false); setLevel(0); setMicName('');
    stopMicrophoneLevel();
    const old = micStream.current; micStream.current = null;
    old?.getAudioTracks().forEach(track => track.stop());
    let pending: MediaStream | null = null;
    try {
      const stream = await openInterviewMedia(next); pending = stream;
      if (!alive.current) { stream.getTracks().forEach(track => track.stop()); return; }
      streams.current.push(stream); attachMicrophone(stream);
      await refreshMicrophones();
    } catch (err) {
      pending?.getTracks().forEach(track => track.stop());
      micStream.current = null; stopMicrophoneLevel();
      if (alive.current) { setMicConnected(false); setLevel(0); setError(microphoneError(err)); }
    }
    finally { if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false; generation.current++;
      if (recorder.current?.state === "recording") recorder.current.stop();
      stopDevices(); detector.current?.close(); detector.current = null;
      if (expiry.current) clearTimeout(expiry.current);
    };
  }, []);
  useEffect(() => () => { if (sampleUrl) URL.revokeObjectURL(sampleUrl); }, [sampleUrl]);
  useEffect(() => {
    if (!cameraGood || !screen) invalidate();
  }, [cameraGood, screen]);

  async function enableDevices() {
    invalidate(); setError(""); setBusy(true); stopDevices(); setDevices(false);
    setScreen(false); setSample(null); setSampleUrl(""); setHeardRecording(false); setPlaybackPlayed(false); setFaces(null);
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Open this page over HTTPS or localhost in a supported desktop browser.");
      const stream = await openInterviewMedia(micChoice.current, true);
      if (!alive.current) { stream.getTracks().forEach(t => t.stop()); return; }
      streams.current.push(stream); attachMicrophone(stream);
      stream.getVideoTracks().forEach(track => track.addEventListener("ended", () => { if (alive.current) { setDevices(false); invalidate(); } }));
      if (video.current) { video.current.srcObject = stream; await video.current.play(); videoCallback.current?.(video.current); }
      if (!alive.current) return;
      await refreshMicrophones();
      if (!alive.current) return;
      if (!detector.current) {
        const engine = await FaceDetector.createFromOptions(await FilesetResolver.forVisionTasks("/preflight/wasm"), {
        baseOptions: { modelAssetPath: "/preflight/blaze_face_short_range.tflite", delegate: "CPU" }, runningMode: "VIDEO", minDetectionConfidence: .6,
        });
        if (!alive.current) { engine.close(); return; }
        detector.current = engine;
      }
      if (alive.current) setDevices(true);
    } catch (err) {
      stopDevices();
      if (alive.current) { await refreshMicrophones(); setError(microphoneError(err)); }
    } finally { if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    if (!devices || !active) return;
    const timer = setInterval(() => {
      const v = video.current;
      if (!v || v.readyState < 2 || !detector.current) return;
      try {
        setFaces(detector.current.detectForVideo(v, performance.now()).detections.length);
        const canvas = document.createElement("canvas"); canvas.width = 64; canvas.height = 48;
        const ctx = canvas.getContext("2d")!; ctx.drawImage(v, 0, 0, 64, 48);
        const pixels = ctx.getImageData(0, 0, 64, 48).data;
        let total = 0;
        for (let i = 0; i < pixels.length; i += 4) total += .299 * pixels[i] + .587 * pixels[i + 1] + .114 * pixels[i + 2];
        setLight(total / (64 * 48) >= 40);
      } catch { setFaces(null); setLight(false); }
    }, 750);
    return () => clearInterval(timer);
  }, [devices, active]);

  async function shareScreen() {
    invalidate(); setError(""); setScreen(false); setBusy(true);
    // Release the old display stream before asking for a replacement.
    const previous = displayStream.current;
    previous?.getTracks().forEach(track => track.stop());
    streams.current = streams.current.filter(stream => stream !== previous);
    displayStream.current = null;
    let pending: MediaStream | null = null;
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("Screen sharing is unavailable. Use a supported desktop browser.");
      const request = entireScreenConstraints();
      pending = await navigator.mediaDevices.getDisplayMedia({ video: request.video, audio: false });
      if (!alive.current) { pending.getTracks().forEach(t => t.stop()); return; }
      requireEntireScreen(pending, request.pickerRestricted);
      const stream = pending;
      displayStream.current = stream;
      streams.current.push(stream); setScreen(true);
      stream.getTracks().forEach(track => track.addEventListener("ended", () => { if (alive.current) { setScreen(false); invalidate(); } }));
    } catch (err) {
      pending?.getTracks().forEach(t => t.stop());
      if (alive.current) {
        const denied = err instanceof DOMException && err.name === "NotAllowedError";
        setError(denied ? "Screen sharing was cancelled or denied. Click Share entire screen and allow access to your display." : err instanceof Error ? err.message : "Screen sharing could not start. Please retry.");
      }
    } finally { if (alive.current) setBusy(false); }
  }
  async function recordSample() {
    invalidate(); setError(""); setSample(null); setSampleUrl(""); setHeardRecording(false); setPlaybackPlayed(false);
    try {
      const source = micStream.current;
      const track = source?.getAudioTracks()[0];
      if (!micConnected || !track?.enabled || track.readyState !== 'live' || isLoopbackInput(track.label)) throw new Error("Select and connect a microphone first.");
      const clone = new MediaStream([track.clone()]);
      const rec = new MediaRecorder(clone); recorder.current = rec; setRecording(true);
      const timeout = setTimeout(() => { if (rec.state === "recording") rec.stop(); }, 4000);
      const blob = await collectRecording(rec); clearTimeout(timeout);
      const wav = await toWav16k(blob);
      if (alive.current) { setSample(wav); setSampleUrl(URL.createObjectURL(wav)); }
    } catch { if (alive.current) setError("The microphone sample could not be recorded. Check the selected microphone and retry."); }
    finally { if (alive.current) setRecording(false); }
  }
  async function playTone() {
    invalidate(); setError(""); setSpeakerConfirmed(false);
    const ctx = new AudioContext();
    try {
      await ctx.resume(); const oscillator = ctx.createOscillator(); const gain = ctx.createGain();
      gain.gain.value = .08; oscillator.frequency.value = 440;
      oscillator.connect(gain); gain.connect(ctx.destination); oscillator.start(); oscillator.stop(ctx.currentTime + .5);
      oscillator.onended = () => { void ctx.close(); if (alive.current) setSpeakerPlayed(true); };
    } catch { void ctx.close(); setError("The test sound could not play. Check your audio output and retry."); }
  }
  async function verify() {
    invalidate(); const version = generation.current; setBusy(true); setError("");
    try {
      const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 240;
      canvas.getContext("2d")!.drawImage(video.current!, 0, 0, 320, 240);
      const frame = await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Camera unavailable")), "image/jpeg", .8));
      const response = await api.verifySetup(token, { policy_version: "setup-v1", face_count: faces, screen_surface: "monitor", speaker_confirmed: speakerConfirmed, playback_confirmed: heardRecording, rules_accepted: rules }, frame, sample!);
      if (!alive.current || version !== generation.current) return;
      setVerified(true); callback.current(response.receipt, micId);
      expiry.current = setTimeout(() => { if (alive.current) invalidate(); }, response.expires_in_seconds * 1000);
    } catch (err) { if (alive.current) setError(err instanceof ApiError ? err.message : "Cannot reach the interview server. Check your connection and retry."); }
    finally { if (alive.current) setBusy(false); }
  }
  const ready = cameraGood && micConnected && screen && sample && heardRecording && speakerConfirmed && rules && !recording;
  const status = (ok: boolean, text: string) => <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${ok ? "bg-emerald-50 text-emerald-800" : "bg-slate-100 text-ink-500"}`}>{ok && <CheckCircle2 size={13} aria-hidden />}{text}</span>;
  return <Card className="p-0 overflow-hidden">
    <div className="border-b border-slate-200 px-6 py-5 md:px-8">
      <h2 className="text-lg font-semibold">Get your devices ready</h2>
      <p className="mt-1 text-sm text-ink-500">Check your camera, audio and screen sharing. Your interview timer has not started.</p>
      <details className="mt-3 text-xs text-ink-500">
        <summary className="w-fit cursor-pointer underline decoration-slate-300 underline-offset-4">How your setup samples are used</summary>
        <p className="mt-2 max-w-2xl leading-relaxed">The camera preview and face check run on your device. Verification sends one camera image and a short audio sample to the interview server; those samples are checked and discarded. Screen sharing is tested but not recorded in this step. These checks do not verify identity.</p>
      </details>
    </div>
    <div className="grid gap-8 p-6 md:grid-cols-[1.15fr_1fr] md:p-8">
      <section className="space-y-4" aria-labelledby="camera-check-title">
        <div className="flex items-center gap-2"><Camera size={18} className="text-brand-700" aria-hidden /><h3 id="camera-check-title" className="font-semibold">Camera & microphone</h3></div>
        <div className="relative overflow-hidden rounded-xl bg-slate-900">
          <video ref={video} muted autoPlay playsInline className="aspect-video w-full object-cover" aria-label="Camera preview" />
          {!devices && <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-300"><Camera size={28} aria-hidden /><p className="text-sm">Your camera preview will appear here</p></div>}
        </div>
        <div className="flex flex-wrap gap-2" role="status" aria-live="polite">
          {status(devices && faces === 1, !devices ? "Camera not checked" : faces === null ? "Checking face…" : faces === 1 ? "One face detected" : faces === 0 ? "Position your face in view" : "More than one face in view")}
          {devices && status(light, light ? "Lighting suitable" : "Improve your lighting")}
        </div>
        <Button variant="secondary" loading={busy} disabled={recording} onClick={() => void enableDevices()}>{devices ? "Reconnect devices" : "Enable camera & microphone"}</Button>
        <div className="space-y-3 border-t border-slate-100 pt-4">
          <Field label="Microphone"><Select value={micId} disabled={recording || busy} onChange={event => void changeMicrophone(event.target.value)}><option value="">Automatic microphone selection</option>{micId && !mics.some(m => m.deviceId === micId) && <option value={micId}>{micName || 'Previously selected microphone'}{!micConnected ? ' (not connected)' : ''}</option>}{mics.filter(m => m.deviceId).map((m, i) => <option key={m.deviceId} value={m.deviceId} disabled={isLoopbackInput(m.label)}>{microphoneLabel(m, i)}</option>)}</Select></Field>
          <div className="flex flex-wrap items-center justify-between gap-2"><p className="min-w-0 flex-1 text-xs text-ink-500 [overflow-wrap:anywhere]" role="status">{micConnected ? `Connected: ${micName}` : 'Choose a microphone, then allow access in the browser.'}</p><Button variant="ghost" className="px-2 py-1 text-xs" disabled={busy || recording} loading={refreshingMics} onClick={() => void refreshMicrophones()}>Refresh microphones</Button></div>
          {isLoopbackInput(micName) && <p className="text-xs text-amber-800">This source captures speaker output. Select your built-in, USB or headset microphone to test your voice.</p>}
          <div><meter className="h-2 w-full" min={0} max={1} value={Math.min(1, level * 6)} aria-label="Microphone input level" /><p className="mt-1 text-xs text-ink-500">Speak a few words and watch the input level.</p></div>
          <Button variant="secondary" disabled={!devices || !micConnected || busy || recording} onClick={() => void recordSample()}>{recording ? "Recording for 4 seconds…" : sample ? "Record sample again" : "Record a 4-second sample"}</Button>
          {sampleUrl && <div className="space-y-3 rounded-lg bg-slate-50 p-3"><audio controls src={sampleUrl} className="h-10 w-full" onEnded={() => { if (alive.current) setPlaybackPlayed(true); }} /><label className="flex items-start gap-2 text-sm"><input className="mt-1 accent-teal-700" type="checkbox" disabled={!playbackPlayed} checked={heardRecording} onChange={e => { invalidate(); setHeardRecording(e.target.checked); }} />I played the sample and can hear my voice clearly</label></div>}
        </div>
      </section>
      <div className="space-y-5">
        <section className="space-y-3 rounded-xl border border-slate-200 p-5" aria-labelledby="speaker-check-title">
          <div className="flex items-center gap-2"><Volume2 size={18} className="text-brand-700" aria-hidden /><h3 id="speaker-check-title" className="font-semibold">Sound output</h3></div>
          <p className="text-sm text-ink-500">Make sure you can hear the interviewer.</p>
          <Button variant="secondary" disabled={busy} onClick={() => void playTone()}>Play test sound</Button>
          <label className="flex items-start gap-2 text-sm"><input className="mt-1 accent-teal-700" type="checkbox" disabled={!speakerPlayed} checked={speakerConfirmed} onChange={e => { invalidate(); setSpeakerConfirmed(e.target.checked); }} />I heard the test sound</label>
        </section>
        <section className="space-y-3 rounded-xl border border-slate-200 p-5" aria-labelledby="screen-check-title">
          <div className="flex items-center gap-2"><Monitor size={18} className="text-brand-700" aria-hidden /><h3 id="screen-check-title" className="font-semibold">Screen sharing</h3></div>
          <p className="text-sm text-ink-500">Select <strong>Entire screen</strong> in the browser picker. A tab or individual window will not pass.</p>
          <Button variant="secondary" disabled={busy} onClick={() => void shareScreen()}>{screen ? "Change shared screen" : "Share entire screen"}</Button>
          <div role="status">{status(screen, screen ? "Entire screen shared" : "Not checked")}</div>
        </section>
        <div className="rounded-lg bg-slate-50 p-4 text-xs leading-relaxed text-ink-500">Need help? Check the camera and microphone permissions in your browser, or contact your interview coordinator.</div>
      </div>
    </div>
    <div className="space-y-4 border-t border-slate-200 bg-slate-50/60 px-6 py-5 md:px-8">
      {error && <Alert tone="error">{error}</Alert>}
      <label className="flex items-start gap-2 text-sm"><input className="mt-1 accent-teal-700" type="checkbox" checked={rules} onChange={e => { invalidate(); setRules(e.target.checked); }} />I understand the setup checks and agree to proceed.</label>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-ink-500">{verified ? "Setup verified. Continue when you are ready." : "Complete all checks to continue to the interview instructions."}</p>
        <Button loading={busy} disabled={!ready} onClick={() => verified ? onContinue() : void verify()}>{verified ? "Continue to instructions" : "Verify & continue"}<ArrowRight size={16} aria-hidden /></Button>
      </div>
    </div>
  </Card>;
}
