import { useEffect, useRef, useState } from 'react';
import { Button } from './ui';
import { GazeCalibration } from './GazeCalibration';
import { classifyGaze, gazeIssue, GazeEpisodes, GAZE_AWAY_MS, type GazeModel } from '../lib/gaze';
import { cameraDevice, watchGaze } from '../lib/gazeCamera';
import type { MonitorEvent } from '../lib/monitor';

export function InterviewGaze({ video, model, active, onEvent }: { video: HTMLVideoElement | null; model: GazeModel | null; active: boolean; onEvent: (event: MonitorEvent) => void }) {
  const [calibration, setCalibration] = useState(model);
  const [calibrating, setCalibrating] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [warning, setWarning] = useState(false);
  const [inactiveHint, setInactiveHint] = useState('Waiting for usable eye estimates.');
  const callback = useRef(onEvent); callback.current = onEvent;
  useEffect(() => { setCalibration(model); }, [model]);
  useEffect(() => {
    const episode = new GazeEpisodes();
    const emit = (kind: string, detail: Record<string, unknown>) => callback.current({ kind, detail, at: new Date().toISOString() });
    setWarning(false); setTracking(false);
    if (!active || !video || !calibration || calibrating) {
      setInactiveHint(!calibration ? 'Complete eye calibration to enable tracking.' : !video ? 'The camera is unavailable. Reconnect your camera.' : calibrating ? 'Calibration is in progress.' : 'Eye tracking is suspended while the interview is paused.');
      emit('gaze.state', { state: calibrating ? 'calibrating' : !calibration ? 'not-calibrated' : 'suspended', review_only: true });
      return;
    }
    emit('gaze.state', { state: 'enabled', review_only: true, validation_error: Number(calibration.error.toFixed(3)), grace_seconds: GAZE_AWAY_MS / 1000 });
    let lastUsable: number | null = null;
    // Brief blinks must not flash the status badge or change the layout.
    const indicator = setInterval(() => setTracking(lastUsable != null && performance.now() - lastUsable < 1500), 250);
    let unknownSince: number | null = null;
    let uncertainReported = false;
    let unavailableReported = false;
    const stop = watchGaze(video, reading => {
      const viewport = { width: window.innerWidth, height: window.innerHeight, camera: cameraDevice(video) };
      const browserInactive = document.hidden || (document.fullscreenEnabled && !document.fullscreenElement);
      const value = browserInactive ? 'unknown' : classifyGaze(calibration, reading, viewport);
      if (value === 'unknown') setInactiveHint(browserInactive ? 'Return to the interview in fullscreen; tracking resumes automatically.' : gazeIssue(calibration, reading, viewport) ?? 'Waiting for reliable eye estimates.');
      if (value !== 'unknown') { lastUsable = performance.now(); setTracking(true); }
      if (value === 'unknown') {
        unknownSince ??= performance.now();
        if (!uncertainReported && performance.now() - unknownSince >= 5000) { uncertainReported = true; emit('gaze.state', { state: 'uncertain', review_only: true }); }
      } else {
        unknownSince = null;
        if (uncertainReported || unavailableReported) { uncertainReported = false; unavailableReported = false; emit('gaze.state', { state: 'enabled', review_only: true }); }
      }
      const transition = episode.update(value, performance.now());
      if (transition) {
        setWarning(transition === 'started');
        emit(`warn.${transition}`, { signal: 'gaze', review_only: true, grace_seconds: GAZE_AWAY_MS / 1000 });
      }
    }, () => { lastUsable = null; setTracking(false); setInactiveHint('Eye tracking was interrupted. Reconnecting automatically; recalibration is not needed for a temporary interruption.'); if (!unavailableReported) { unavailableReported = true; emit('gaze.state', { state: 'unavailable', review_only: true }); } });
    return () => {
      stop();
      clearInterval(indicator);
      if (episode.reset()) emit('warn.cleared', { signal: 'gaze', reason: 'tracking-suspended', review_only: true });
    };
  }, [video, calibration, active, calibrating]);
  const enabled = tracking && active && !!video && !!calibration && !calibrating;
  return <div className="mt-3 border-t border-slate-200 pt-3 text-xs">
    <div className="flex items-center justify-between gap-2"><p className="font-medium">Eye tracking</p><span role="status" tabIndex={0} aria-live="off" aria-label={`Eye tracking ${enabled ? 'active' : 'not active'}`} title={enabled ? 'Receiving usable eye estimates. Brief glances are allowed.' : inactiveHint} className={`inline-flex h-6 w-24 shrink-0 items-center justify-center gap-1.5 rounded-full font-medium ${enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-ink-500'}`}><span aria-hidden className={`h-1.5 w-1.5 rounded-full ${enabled ? 'bg-emerald-600' : 'bg-slate-400'}`} />{enabled ? 'Active' : 'Not active'}</span></div>
    {warning && <p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 leading-relaxed text-amber-800">Please return your gaze to the interview screen. A sustained look-away was noted for review.</p>}
    <Button variant="ghost" className="mt-2 px-0 py-1 text-xs" disabled={!video || !active} onClick={() => setCalibrating(true)}>{calibration ? 'Recalibrate viewing area' : 'Calibrate viewing area'}</Button>
    {calibrating && <div className="fixed inset-0 z-40 overflow-y-auto bg-slate-50 p-6"><div className="mx-auto max-w-3xl"><GazeCalibration duringInterview video={video} onBack={() => setCalibrating(false)} onComplete={fresh => { setCalibration(fresh); setCalibrating(false); }} /></div></div>}
  </div>;
}
