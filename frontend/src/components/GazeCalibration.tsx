import { useEffect, useRef, useState } from 'react';
import { Eye, RotateCcw } from 'lucide-react';
import { Alert, Button, Card } from './ui';
import { fitGaze, GAZE_AWAY_MS, GAZE_TARGETS, GAZE_VALIDATION, validateGaze, type GazeModel, type GazeReading, type GazeSample } from '../lib/gaze';
import { cameraDevice, watchGaze } from '../lib/gazeCamera';

/** Calibration never starts the interview timer or sends eye samples to a server. */
export function GazeCalibration({ video, onComplete, onBack, duringInterview = false }: { duringInterview?: boolean; video: HTMLVideoElement | null; onComplete: (model: GazeModel) => void; onBack: () => void }) {
  const [phase, setPhase] = useState<'intro' | 'running' | 'failed'>('intro');
  const [step, setStep] = useState(0);
  const [count, setCount] = useState(0);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const samples = useRef<GazeSample[]>([]);
  const checks = useRef<GazeSample[]>([]);
  const reading = useRef<GazeReading | null>(null);
  const lastReading = useRef(0);
  const model = useRef<GazeModel | null>(null);
  const stepRef = useRef(0), pointSince = useRef(0);
  const viewport = useRef({ width: 0, height: 0 });
  const callbacks = useRef({ onComplete, onBack }); callbacks.current = { onComplete, onBack };
  const points = [...GAZE_TARGETS, ...GAZE_VALIDATION];
  const running = phase === 'running';
  useEffect(() => {
    if (!running || !video) return;
    const stop = watchGaze(video, value => { reading.current = value; lastReading.current = performance.now(); }, () => {
      setError('Eye tracking could not start in this browser. Check your camera and browser, then retry. Calibration must pass before you can continue.'); setPhase('failed');
    });
    const timer = setInterval(() => {
      if (document.hidden || (document.fullscreenEnabled && !document.fullscreenElement) || Math.abs(window.innerWidth - viewport.current.width) > 30 || Math.abs(window.innerHeight - viewport.current.height) > 30) {
        setError('The screen changed during calibration. Keep this window in fullscreen and retry.'); setPhase('failed'); return;
      }
      const index = stepRef.current;
      if (index >= points.length || performance.now() - pointSince.current < 700 || !reading.current || performance.now() - lastReading.current > 250) return;
      const sample = { target: points[index], reading: reading.current };
      // Each camera result is used once, regardless of rendering/timer speed.
      reading.current = null;
      const collection = index < GAZE_TARGETS.length ? samples.current : checks.current;
      collection.push(sample);
      const captured = collection.filter(s => s.target === points[index]).length;
      setCount(captured);
      if (captured < 12) return;
      try {
        if (index === GAZE_TARGETS.length - 1) model.current = fitGaze(samples.current, { ...viewport.current, camera: cameraDevice(video) });
        if (index === points.length - 1) {
          const calibrated = validateGaze(model.current!, checks.current);
          stop(); clearInterval(timer); callbacks.current.onComplete(calibrated); return;
        }
        stepRef.current++; pointSince.current = performance.now(); setStep(stepRef.current); setCount(0);
      } catch (err) { setError((err as Error).message); setPhase('failed'); }
    }, 125);
    const deadline = setTimeout(() => { setError('Calibration timed out. Improve the camera position or lighting, then retry. Calibration must pass before you can continue.'); setPhase('failed'); }, 100_000);
    return () => { stop(); clearInterval(timer); clearTimeout(deadline); };
  }, [running, video, attempt]);

  async function begin() {
    setError('');
    try {
      if (!video || video.readyState < 2) throw new Error('Reconnect your camera in the system check, then return here.');
      if (document.fullscreenEnabled && !document.fullscreenElement) await document.documentElement.requestFullscreen();
      viewport.current = { width: window.innerWidth, height: window.innerHeight };
      samples.current = []; checks.current = []; reading.current = null; model.current = null;
      stepRef.current = 0; pointSince.current = performance.now(); setStep(0); setCount(0); setAttempt(v => v + 1); setPhase('running');
    } catch (err) { setError((err as Error).message); setPhase('failed'); }
  }
  if (running) {
    const target = points[step];
    return <div className="fixed inset-0 z-50 bg-slate-950 text-white" role="dialog" aria-modal="true" aria-label="Eye tracking calibration">
      <p className="absolute left-1/2 top-4 w-4/5 -translate-x-1/2 text-center text-sm text-slate-300">Look at the dot. Keep your head comfortable and still; move your eyes naturally.</p>
      <div style={{ left: `${target.x * 100}%`, top: `${target.y * 100}%` }} className="absolute -translate-x-1/2 -translate-y-1/2" aria-label={`Look at ${target.name}`}>
        <div className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-teal-300 bg-teal-300/15"><div className="h-3 w-3 rounded-full bg-teal-200" /></div>
      </div>
      <div className="absolute bottom-4 left-1/2 w-4/5 -translate-x-1/2 text-center"><p className="text-sm" role="status">{step < 9 ? `Calibration ${step + 1} of 9` : `Accuracy check ${step - 8} of 5`} · {Math.min(count, 12)} / 12 samples</p><p className="mt-1 text-xs text-slate-300">{count ? 'Keep looking at the dot.' : 'Waiting for a clear view of your eyes.'}</p><button className="mt-3 rounded border border-slate-600 px-3 py-1 text-sm" onClick={() => setPhase('intro')}>Cancel calibration</button></div>
    </div>;
  }
  return <Card className="space-y-6 p-6 md:p-8">
    <div><Eye size={25} className="mb-4 text-brand-700" aria-hidden /><h2 className="text-xl font-semibold">Set your comfortable viewing area</h2><p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-500">Follow nine dots with your eyes, then complete a short accuracy check. Keep your camera close to the screen at eye level. If you use a phone camera, secure it in position first.</p></div>
    <div className="rounded-xl bg-slate-50 p-5 text-sm leading-relaxed"><p>Natural glances and blinks are expected. A gaze reminder is issued only after more than {GAZE_AWAY_MS / 1000} seconds of reliably detected look-away, with extra room around the screen edges.</p><p className="mt-3 text-ink-500">Eye estimates stay on your device. Only a warning snapshot and timestamp are saved for HR review. Gaze observations do not change scores or automatically end the interview. Complete calibration and the accuracy check to continue.</p></div>
    {error && <Alert tone="warning">{error}</Alert>}
    <div className="flex flex-wrap items-center gap-3"><Button onClick={() => void begin()}>{phase === 'failed' ? <RotateCcw size={16} aria-hidden /> : <Eye size={16} aria-hidden />}{phase === 'failed' ? 'Retry calibration' : 'Begin calibration'}</Button><Button variant="ghost" onClick={onBack}>{duringInterview ? 'Back to interview' : 'Back to system check'}</Button></div>
    <p className="text-xs text-ink-500">{duringInterview ? 'Your interview timer continues during recalibration.' : 'Required before interview instructions · about 45 seconds · interview timer has not started'}</p>
  </Card>;
}
