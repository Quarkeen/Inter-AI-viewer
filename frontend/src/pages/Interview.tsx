import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api, type InterviewState, type InterviewTurn } from "../lib/api";
import { formatSlot } from "../lib/format";
import { collectRecording, listMicrophones, recordAnswerMic, toWav16k, watchMicLevel } from "../lib/audio";
import { useMonitorQueue, violationStage, WARN_GRACE_MS, watchBrowserEvents, type MonitorEvent } from "../lib/monitor";
import { useSpeech } from "../lib/speech";
import { Alert, Button, Card, Field, PageTitle, Progress, Select, Spinner, Thinking } from "../components/ui";

import { InterviewSetup } from "../components/InterviewSetup";
import { InterviewCamera } from "../components/InterviewCamera";
import { InterviewInstructions } from "../components/InterviewInstructions";
import { CheckCircle2 } from "lucide-react";
import type { WarningSnapshot } from '../lib/warningSnapshot';
import { isLoopbackInput, microphoneError, microphoneLabel } from '../lib/microphones';
import { GazeCalibration } from '../components/GazeCalibration';
import { InterviewGaze } from '../components/InterviewGaze';
import { hasGazeCalibration, type GazeModel } from '../lib/gaze';

function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function QuestionCard({ turn, readAloud }: { turn: InterviewTurn; readAloud: boolean }) {
  const { speak, stop, supported, wordsRef, statusRef } = useSpeech(turn.question, turn.turn_id, readAloud);
  const words = turn.question.match(/\S+\s*/g) ?? [turn.question];
  wordsRef.current = [];
  return (
    <div className="rounded-2xl border border-slate-700 bg-slate-900 p-6 text-slate-50">
      <p className="mb-3 text-xs font-semibold tracking-widest text-teal-300">YOUR INTERVIEWER</p>
      <p className="text-xl leading-relaxed" aria-live="polite">
        {words.map((word, index) => (
          <span
            key={index}
            ref={(span) => {
              if (span) wordsRef.current[index] = span;
            }}
          >
            {word}
          </span>
        ))}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={speak} disabled={!supported}>
          ▶ Read aloud
        </Button>
        <Button variant="secondary" onClick={stop}>
          ■ Stop
        </Button>
        <span ref={statusRef} className="text-xs text-slate-400">
          Read or listen, then record your answer.
        </span>
      </div>
    </div>
  );
}

export function Interview() {
  const [params, setParams] = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<InterviewState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [setupReceipt, setSetupReceipt] = useState<string | null>(null);
  const [setupVideo, setSetupVideo] = useState<HTMLVideoElement | null>(null);
  const [liveVideo, setLiveVideo] = useState<HTMLVideoElement | null>(null);
  const [gazeModel, setGazeModel] = useState<GazeModel | null>(null);
  const gazeReady = hasGazeCalibration(gazeModel);
  const [stage, setStage] = useState<"thinking" | "transcribing" | null>(null);
  const [readAloud, setReadAloud] = useState(true);
  const [recording, setRecording] = useState(false);
  const [recorded, setRecorded] = useState<Blob | null>(null);
  const [recordUrl, setRecordUrl] = useState<string | null>(null);
  const [recordSecs, setRecordSecs] = useState<number | null>(null);
  const [recorder, setRecorder] = useState<MediaRecorder | null>(null);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [micId, setMicId] = useState("");
  const [level, setLevel] = useState(0);
  const [fullscreenOk, setFullscreenOk] = useState(() => document.fullscreenEnabled !== true || document.fullscreenElement != null);
  const [cameraEnded, setCameraEnded] = useState(false);
  const [cameraBypassed, setCameraBypassed] = useState(false);
  const [cameraKey, setCameraKey] = useState(0);
  const [pauseDebt, setPauseDebt] = useState(false);
  const [pauseRetry, setPauseRetry] = useState(0);
  const [violations, setViolations] = useState<{ fsSince: number | null; faceSince: number | null }>({
    fsSince: null,
    faceSince: null,
  });
  const violationsRef = useRef(violations);
  violationsRef.current = violations;
  const cameraLiveRef = useRef(false);
  const [terminateRetry, setTerminateRetry] = useState(0);
  const terminateFiredFor = useRef<string | null>(null);
  const enteredFullscreen = useRef(document.fullscreenElement != null);
  const resuming = useRef(false);
  const clientPausedAt = useRef<number | null>(null);
  const recordingRef = useRef({ recording: false, recorder: null as MediaRecorder | null });
  recordingRef.current = { recording, recorder };
  const recordStart = useRef(0);
  const levelStop = useRef<(() => void) | null>(null);
  const now = useNow(250);
  const waitingTick = useNow(15000);
  const mounted = useRef(true);
  const preparationHeading = useRef<HTMLDivElement>(null);
  const snapshotRef = useRef<(() => WarningSnapshot | null) | null>(null);
  const loadSequence = useRef(0);

  const instructions = params.get("step") === "instructions" && !!setupReceipt && gazeReady;
  const calibrating = !!setupReceipt && (params.get('step') === 'calibration' || (params.get('step') === 'instructions' && !gazeReady));
  useEffect(() => { preparationHeading.current?.focus(); }, [instructions, calibrating, !!state]);
  function showStep(step: "setup" | "calibration" | "instructions", replace = false) {
    const next = new URLSearchParams(params);
    if (step !== "setup") next.set("step", step); else next.delete("step");
    setParams(next, { replace });
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  useEffect(() => {
    if (params.has("step") && !setupReceipt) showStep("setup", true);
  }, [params, setupReceipt]);

  const load = useCallback(
    async (quiet = false) => {
      const sequence = ++loadSequence.current;
      try {
        const fresh = await api.interviewState(token);
        if (mounted.current && sequence === loadSequence.current) setState(fresh);
      } catch (err) {
        if (mounted.current && sequence === loadSequence.current && !quiet)
          setError(err instanceof ApiError ? err.message : "Your interview could not be loaded. Please refresh and try again.");
      }
    },
    [token],
  );

  useEffect(() => {
    mounted.current = true;
    void load();
    listMicrophones()
      .then((devices) => {
        if (mounted.current) setMics(devices);
      })
      .catch(() => {});
    return () => {
      mounted.current = false;
      levelStop.current?.();
      levelStop.current = null;
    };
  }, [load]);

  // Waiting room refresh, like the original 15s fragment.
  useEffect(() => {
    if (state && !state.session && state.access.state === "early") void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitingTick]);

  const liveSessionId = state?.session?.status === "active" ? state.session.session_id : null;
  const reportMonitorEvent = useMonitorQueue(token, liveSessionId, {
    capture: () => snapshotRef.current?.() ?? null,
    activeWarnings: state?.proctoring?.active_warnings,
    onWarningsSynced: () => void load(true),
  });
  useLayoutEffect(() => {
    if (!liveSessionId) return;
    return watchBrowserEvents(reportMonitorEvent);
  }, [liveSessionId, reportMonitorEvent]);

  // Pause enforcement: fullscreen is required while the interview runs, and a lost
  // camera freezes it until reconnected. The server owns the timer credit; the client
  // only reports what it observes. Resume is automatic once conditions are restored.
  const canFullscreen = document.fullscreenEnabled === true;
  useEffect(() => {
    const syncFullscreen = () => {
      const current = !canFullscreen || document.fullscreenElement != null;
      setFullscreenOk(current);
      if (current) enteredFullscreen.current = true;
    };
    // Read immediately: a transition may precede this listener being attached.
    syncFullscreen();
    document.addEventListener("fullscreenchange", syncFullscreen);
    window.addEventListener("focus", syncFullscreen);
    window.addEventListener("resize", syncFullscreen);
    const poll = window.setInterval(syncFullscreen, 250);
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreen);
      window.removeEventListener("focus", syncFullscreen);
      window.removeEventListener("resize", syncFullscreen);
      window.clearInterval(poll);
    };
  }, [canFullscreen, liveSessionId]);

  const stopRecordingForPause = useCallback(() => {
    const current = recordingRef.current;
    if (current.recording && current.recorder) {
      current.recorder.stop();
      levelStop.current?.();
      levelStop.current = null;
      setLevel(0);
      setRecording(false);
    }
  }, []);
  useEffect(() => {
    if (state?.session && state.session.status !== 'active') stopRecordingForPause();
  }, [state?.session?.status, stopRecordingForPause]);

  const serverPaused = state?.session?.status === "active" && state.session.paused_at != null;
  const pauseReason = state?.session?.pause_reason ?? null;
  const cameraBlocked = cameraEnded && !cameraBypassed;
  // Re-read before enforcing a deadline: React state may lag a browser transition.
  const fsStage = (!canFullscreen || document.fullscreenElement != null) ? "ok" : violationStage(violations.fsSince, now);
  const faceStage = violationStage(violations.faceSince, now);
  const needsPause = liveSessionId != null && (fsStage === "pause" || faceStage === "pause" || cameraBlocked);
  const paused = serverPaused || needsPause;

  const handleCameraEvent = useCallback(
    (event: MonitorEvent) => {
      reportMonitorEvent(event);
      if (event.kind === "camera.state") {
        if (event.detail.state === 'ended' || event.detail.state === 'unavailable') {
          reportMonitorEvent({kind: 'warn.started', detail: {signal: 'camera'}, at: event.at});
          reportMonitorEvent({kind: 'warn.cleared', detail: {signal: 'face'}, at: event.at});
        } else if (event.detail.state === 'started') {
          reportMonitorEvent({kind: 'warn.cleared', detail: {signal: 'camera'}, at: event.at});
        }
        if (event.detail.state === "ended") {
          cameraLiveRef.current = false;
          setCameraEnded(true);
          // Presence is unobservable without a camera; drop any absence episode.
          setViolations(current => ({ ...current, faceSince: null }));
        } else {
          setCameraEnded(false);
          if (event.detail.state === "started") {
            cameraLiveRef.current = true;
            setCameraBypassed(false);
          } else {
            cameraLiveRef.current = false;
            setViolations(current => ({ ...current, faceSince: null }));
          }
        }
        return;
      }
      if (event.kind !== "face.count" || !liveSessionId) return;
      if (event.detail.count === 0) {
        if (paused || !cameraLiveRef.current || violationsRef.current.faceSince != null) return;
        setViolations(current => ({ ...current, faceSince: Date.now() }));
        reportMonitorEvent({ kind: "warn.started", detail: { signal: "face" }, at: new Date().toISOString() });
      } else if (typeof event.detail.count === 'number' && event.detail.count > 0) {
        if (violationsRef.current.faceSince != null) setViolations(current => ({ ...current, faceSince: null }));
        reportMonitorEvent({ kind: "warn.cleared", detail: { signal: "face" }, at: new Date().toISOString() });
      }
    },
    [reportMonitorEvent, liveSessionId, paused],
  );
  useEffect(() => {
    if (!liveSessionId) return;
    if (!fullscreenOk && violations.fsSince == null && !paused) {
      setViolations(current => ({ ...current, fsSince: Date.now() }));
      reportMonitorEvent({ kind: "warn.started", detail: { signal: "fullscreen" }, at: new Date().toISOString() });
    } else if (fullscreenOk) {
      if (violations.fsSince != null) setViolations(current => ({ ...current, fsSince: null }));
      reportMonitorEvent({ kind: "warn.cleared", detail: { signal: "fullscreen" }, at: new Date().toISOString() });
    }
  }, [liveSessionId, paused, fullscreenOk, violations.fsSince, reportMonitorEvent]);

  const fullscreenReason = enteredFullscreen.current ? "fullscreen-exit" : "fullscreen-required";
  useEffect(() => {
    if (fullscreenOk) enteredFullscreen.current = true;
  }, [fullscreenOk]);

  useEffect(() => {
    if (!needsPause || !liveSessionId || serverPaused) return;
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    clientPausedAt.current ??= Date.now();
    stopRecordingForPause();
    const reason = fsStage === "pause" ? fullscreenReason : cameraBlocked ? "camera-ended" : "face-absent";
    setPauseDebt(true);
    reportMonitorEvent({ kind: "pause.started", detail: { reason }, at: new Date().toISOString() });
    void api
      .pauseInterview(token, reason)
      .then(() => { if (!cancelled) return load(true); })
      .catch(() => {})
      .finally(() => { if (!cancelled) retry = setTimeout(() => setPauseRetry(value => value + 1), 3000); });
    return () => { cancelled = true; clearTimeout(retry); };
    // The booleans are the transition; the rest is stable for the session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsPause, liveSessionId, serverPaused, pauseRetry]);

  const resumeNow = useCallback(
    async (extra?: Record<string, unknown>) => {
      if (resuming.current) return;
      resuming.current = true;
      try {
        await api.resumeInterview(token);
      } catch {
        resuming.current = false;
        setNotice("Could not resume yet. Check your connection and retry.");
        return;
      }
      reportMonitorEvent({ kind: "pause.ended", detail: { reason: pauseReason, ...extra }, at: new Date().toISOString() });
      setPauseDebt(false);
      clientPausedAt.current = null;
      try {
        await load(true);
      } finally {
        resuming.current = false;
      }
    },
    [token, pauseReason, reportMonitorEvent, load],
  );
  useEffect(() => {
    if (needsPause || !liveSessionId || (!serverPaused && !pauseDebt)) return;
    void resumeNow();
  }, [needsPause, serverPaused, liveSessionId, pauseDebt, resumeNow]);
  useEffect(() => {
    const deadline = state?.session?.terminate_at;
    if (!liveSessionId || !deadline || terminateFiredFor.current === deadline) return;
    if (Date.now() < new Date(deadline).getTime()) return;
    terminateFiredFor.current = deadline;
    void (async () => {
      try {
        const result = await api.terminateInterview(token);
        if (!result.terminated) {
          terminateFiredFor.current = null;
          window.setTimeout(() => setTerminateRetry(tick => tick + 1), result.remaining_seconds * 1000 + 1000);
        }
      } catch {
        terminateFiredFor.current = null;
        window.setTimeout(() => setTerminateRetry(tick => tick + 1), 5000);
      }
      await load(true);
    })();
    // `now` ticks this check; the fired-guard keeps the call single per deadline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveSessionId, state?.session?.terminate_at, terminateRetry, now]);

  const enterFullscreen = async () => {
    try {
      await document.documentElement.requestFullscreen();
      const current = document.fullscreenElement != null;
      setFullscreenOk(current);
      if (current) enteredFullscreen.current = true;
    } catch {
      setNotice("Fullscreen was blocked by the browser. Allow fullscreen for this site and retry.");
    }
  };
  const resumeWithoutCamera = () => {
    void resumeNow({ override: true });
    setCameraBypassed(true);
  };

  async function runAction(audio: Blob | null, turnId: number | null) {
    if (paused) return;
    setBusy(true);
    setNotice(null);
    setStage(audio ? "transcribing" : "thinking");
    try {
      const fresh = await api.advanceInterview(token, turnId, audio);
      setState(fresh);
      setRecorded(null);
      if (audio && recordUrl) {
        URL.revokeObjectURL(recordUrl);
        setRecordUrl(null);
        setRecordSecs(null);
      }
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "The interviewer could not continue. Your saved progress is safe; please retry.");
      await load();
    } finally {
      if (mounted.current) {
        setBusy(false);
        setStage(null);
      }
    }
  }

  async function start() {
    setBusy(true);
    setNotice(null);
    try {
      if (!setupReceipt) throw new Error("Complete the system check first.");
      if (!hasGazeCalibration(gazeModel)) throw new Error('Complete eye calibration and the accuracy check before starting.');
      // Request while the Start click still has user activation, before network I/O.
      if (document.fullscreenEnabled === true && !document.fullscreenElement) {
        try {
          await document.documentElement.requestFullscreen();
          if (!document.fullscreenElement) throw new Error("Fullscreen was not entered.");
        } catch {
          setNotice("Enter fullscreen to start. Allow fullscreen for this site, then click Start interview again.");
          return;
        }
      }
      setFullscreenOk(document.fullscreenEnabled !== true || document.fullscreenElement != null);
      enteredFullscreen.current = document.fullscreenElement != null;
      setViolations(current => ({ ...current, fsSince: null }));
      await api.startSession(token, setupReceipt);
      await runAction(null, null);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Your interview could not be started. Please try again.");
      await load();
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function toggleRecording() {
    if (paused) return;
    setRecordError(null);
    if (recording && recorder) {
      recorder.stop();
      levelStop.current?.();
      levelStop.current = null;
      setLevel(0);
      setRecording(false);
      return;
    }
    try {
      if (recordUrl) URL.revokeObjectURL(recordUrl);
      setRecordUrl(null);
      setRecordSecs(null);
      const mic = await recordAnswerMic(micId || undefined);
      const actualMic = mic.stream.getAudioTracks()[0]?.getSettings().deviceId;
      if (actualMic) setMicId(actualMic);
      levelStop.current?.();
      levelStop.current = watchMicLevel(mic.stream, (value) => {
        if (mounted.current) setLevel(value);
      });
      // Device labels appear once permission is granted — refresh the list.
      listMicrophones()
        .then((devices) => {
          if (mounted.current && devices.length > 0) setMics(devices);
        })
        .catch(() => {});
      setRecorder(mic);
      setRecording(true);
      setRecorded(null);
      recordStart.current = Date.now();
      const blob = await collectRecording(mic);
      const wav = await toWav16k(blob);
      if (mounted.current) {
        setRecorded(wav);
        setRecordUrl(URL.createObjectURL(wav));
        setRecordSecs(Math.round((Date.now() - recordStart.current) / 100) / 10);
      }
    } catch (err) {
      levelStop.current?.();
      levelStop.current = null;
      setLevel(0);
      if (mounted.current) {
        setRecordError(microphoneError(err));
        setRecording(false);
      }
    }
  }

  if (!token) return <Alert tone="error">This invitation is unavailable. Please contact the hiring team.</Alert>;
  if (error) return <Alert tone="error">{error}</Alert>;
  if (!state) return <Spinner label="Loading your interview…" />;

  const { access, session, turns } = state;
  const mainLimit = state.config?.main_questions ?? 6;
  const followLimit = state.config?.max_followups ?? 4;
  if (access.state === "rescheduled") {
    return (
      <div>
        <PageTitle title="Your interview has been rescheduled" />
        <Alert tone="info">
          This invitation link is no longer active. You will receive updated interview details and a new link by email.
        </Alert>
        <p className="mt-2 text-sm text-ink-500">
          Please use the latest invitation from the recruitment team. Contact them if you have not received the updated email.
        </p>
      </div>
    );
  }
  if (access.state === "invalid") {
    return (
      <div>
        <PageTitle title="Interview invitation" />
        <Alert tone="error">This invitation is unavailable. Please contact the hiring team.</Alert>
      </div>
    );
  }
  if (session && session.status === "terminated") {
    return (
      <div>
        <PageTitle title="Your interview has ended" />
        <Alert tone="warning">
          {session.pause_reason === 'warning-limit'
            ? 'The interview ended because the allowed fullscreen/face-absence warning count was exceeded.'
            : session.pause_reason === "face-absent"
            ? "The interview ended because you were out of the camera frame for too long."
            : "The interview ended because fullscreen was not restored in time."}{" "}
          Please contact the hiring team if you need help with your invitation.
        </Alert>
      </div>
    );
  }
  if (session && (session.status === "completed" || session.status === "expired")) {
    return (
      <div>
        <PageTitle title="Thank you for your time" />
        <Alert tone="success">{state.completion_message}</Alert>
      </div>
    );
  }
  if (!session && access.state === "early") {
    return (
      <div>
        <PageTitle title="Your interview is scheduled" />
        <Alert tone="info">Your interview has not started yet.</Alert>
        <p className="mt-2 text-sm">
          Your slot opens at <b>{access.opens_at ? formatSlot(access.opens_at) : ""}</b>.
        </p>
        <p className="mt-1 text-xs text-ink-500">This page checks your slot automatically. Keep your invitation private.</p>
      </div>
    );
  }
  if (access.state === "expired") {
    return (
      <div>
        <PageTitle title="Interview invitation" />
        <Alert tone="warning">The window to start this interview has closed.</Alert>
        <p className="mt-1 text-xs text-ink-500">Please contact the hiring team if you need help with your invitation.</p>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="mx-auto max-w-4xl">
        <header className="mb-7">
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-brand-700">Interview preparation</p>
          <ol className="mb-7 flex flex-wrap items-center gap-3 text-xs sm:gap-5 sm:text-sm" aria-label="Interview progress">
            {["System check", "Viewing area", "Instructions", "Interview"].map((label, index) => {
              const currentStep = instructions ? 2 : calibrating ? 1 : 0;
              const active = index === currentStep;
              const complete = index < currentStep;
              return <li key={label} aria-current={active ? "step" : undefined} className={`flex items-center gap-2 ${active ? "font-semibold text-brand-700" : "text-ink-500"}`}>
                <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${active ? "bg-brand-600 text-white" : complete ? "bg-emerald-50 text-emerald-700" : "bg-slate-200"}`}>{complete ? <CheckCircle2 size={16} aria-hidden /> : index + 1}</span>{label}
                {index < 3 && <span className="ml-1 hidden h-px w-8 bg-slate-200 sm:block" aria-hidden />}
              </li>;
            })}
          </ol>
          <div ref={preparationHeading} tabIndex={-1} className="outline-none"><PageTitle title={instructions ? "Interview instructions" : calibrating ? 'Viewing area calibration' : "System check"} caption={instructions ? "Your setup is ready. Review the instructions before starting." : calibrating ? 'Help the camera learn your comfortable screen-viewing area.' : "Let's make sure everything is working before your interview."} /></div>
        </header>
        {/* Keep the media checks mounted so navigating to instructions preserves
            the live streams and still invalidates expired or disconnected checks. */}
        <section hidden={instructions || calibrating} aria-label="System check">
          <InterviewSetup key={token} token={token} active={!instructions && !calibrating} onVideoReady={setSetupVideo}
            onContinue={() => showStep(gazeReady ? 'instructions' : 'calibration')}
            onReady={(receipt, selectedMic) => {
              setSetupReceipt(receipt); setMicId(selectedMic);
              if (receipt) showStep(gazeReady ? 'instructions' : 'calibration');
              else { setGazeModel(null); if (params.has('step')) showStep('setup', true); }
            }} />
        </section>
        {calibrating && <GazeCalibration video={setupVideo} onBack={() => showStep('setup')} onComplete={model => { if (!hasGazeCalibration(model)) return; setGazeModel(model); showStep('instructions'); }} />}
        {instructions && <InterviewInstructions warningLimit={state.proctoring?.terminate_on_interruption ? state.proctoring.warning_limit : undefined} mainQuestions={mainLimit} maxFollowups={followLimit} duration={access.duration_minutes} opensAt={access.opens_at} closesAt={access.closes_at}
          busy={busy} onBack={() => showStep('setup')} onStart={() => void start()} />}
        {notice && <div className="mt-4"><Alert tone="warning">{notice}</Alert></div>}
      </div>
    );
  }

  const current = turns[turns.length - 1];
  const mainsAnswered = turns.filter((turn) => turn.kind === "main" && turn.answer_text != null).length;
  const follows = turns.filter((turn) => turn.kind === "follow_up").length;
  const pauseAnchorMs = session.paused_at ? new Date(session.paused_at).getTime() : clientPausedAt.current;
  const heldMs = paused && pauseAnchorMs ? Math.max(0, now - pauseAnchorMs) : 0;
  const seconds = Math.max(0, Math.floor((new Date(session.slot_end).getTime() + heldMs - now) / 1000));
  const answered = turns.filter((turn) => turn.answer_text);
  const blocker =
    fsStage === "pause" ? "fullscreen" : cameraBlocked ? "camera" : faceStage === "pause" ? "face" : serverPaused ? "resuming" : null;
  const terminateSecs =
    session.terminate_at != null ? Math.max(0, Math.ceil((new Date(session.terminate_at).getTime() - now) / 1000)) : null;

  return (
    <div>
      <p className="mb-2 text-xs font-semibold tracking-widest text-brand-700">INTERVIEW ROOM</p>
      <PageTitle title={session.job_title} caption={`Welcome, ${session.full_name}. Take a moment to think before you answer.`} />
      <div className="mb-4 grid gap-3 md:grid-cols-[3fr_1fr]">
        <Progress value={mainsAnswered / mainLimit} label={`Main questions answered: ${mainsAnswered}/${mainLimit} · Follow-ups: ${follows}/${followLimit}`} />
        <div className="space-y-3">
          <Card className="py-3">
            <p className="text-xs text-ink-500">Time remaining</p>
            <p className="text-2xl font-semibold tabular-nums">
              {String(Math.floor(seconds / 60)).padStart(2, "0")}:{String(seconds % 60).padStart(2, "0")}
            </p>
          </Card>
          <div><InterviewCamera key={cameraKey} onEvent={handleCameraEvent} snapshotRef={snapshotRef} onVideoReady={setLiveVideo} /><InterviewGaze video={liveVideo} model={gazeModel} active={!paused} onEvent={reportMonitorEvent} /></div>
        </div>
      </div>
      {notice && (
        <div className="mb-3">
          <Alert tone="warning">{notice}</Alert>
        </div>
      )}
      {busy && <Thinking message={stage === "transcribing" ? "Transcribing your answer" : "Preparing your next question"} />}
      {state.proctoring?.terminate_on_interruption && <p className="mb-3 text-sm text-ink-500">Warnings: {state.proctoring.warning_count} of {state.proctoring.warning_limit} allowed. The next warning after this allowance ends the interview.</p>}
      {!paused && (fsStage === "warn" || faceStage === "warn") && (
        <div className="mb-3 space-y-2" role="alert">
          {fsStage === "warn" && violations.fsSince != null && (
            <Alert tone="warning">
              You left fullscreen —
              return within {Math.max(0, Math.ceil((WARN_GRACE_MS - (now - violations.fsSince)) / 1000))}s or the interview will pause.
              <div className="mt-2"><Button variant="secondary" onClick={() => void enterFullscreen()}>Return to fullscreen</Button></div>
            </Alert>
          )}
          {faceStage === "warn" && violations.faceSince != null && (
            <Alert tone="warning">
              You are out of the camera frame —
              return within {Math.ceil((WARN_GRACE_MS - (now - violations.faceSince)) / 1000)}s or the interview will pause.
            </Alert>
          )}
        </div>
      )}
      {blocker ? (
        <Card>
          <h2 className="text-lg font-semibold">
            {blocker === "fullscreen" && !enteredFullscreen.current ? "Enter fullscreen to begin" : "Interview paused"}
          </h2>
          <p className="mt-1 text-sm text-ink-500">
            {blocker === "fullscreen" &&
              !enteredFullscreen.current &&
              "Fullscreen is required for this interview. Your timer is held until you enter it."}
            {blocker === "fullscreen" &&
              enteredFullscreen.current &&
              "You left fullscreen. Re-enter fullscreen to resume — your timer is held."}
            {blocker === "camera" && "Your camera disconnected. Reconnect it to resume — your timer is held."}
            {blocker === "face" && "You have been out of the camera frame. Return to the frame to resume — your timer is held."}
            {blocker === "resuming" && "Restoring your interview…"}
          </p>
          {terminateSecs != null && (
            <p className="mt-2 text-sm font-semibold text-red-700" role="timer">
              Interview ends in {String(Math.floor(terminateSecs / 60))}:{String(terminateSecs % 60).padStart(2, "0")} unless
              conditions are restored.
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-3">
            {blocker === "fullscreen" && <Button onClick={() => void enterFullscreen()}>Enter fullscreen to continue</Button>}
            {blocker === "camera" && <Button onClick={() => setCameraKey(key => key + 1)}>Reconnect camera</Button>}
            {blocker === "camera" && (
              <Button variant="secondary" onClick={resumeWithoutCamera}>
                Continue without camera
              </Button>
            )}
            {blocker === "resuming" && <Button onClick={() => void resumeNow()}>Resume interview</Button>}
          </div>
          <p className="mt-3 text-xs text-ink-500">Pause credit is capped at 10 minutes in total. Every pause is noted for review.</p>
        </Card>
      ) : !current ? (
        <Button loading={busy} onClick={() => void runAction(null, null)}>
          Prepare first question
        </Button>
      ) : current.answer_text == null ? (
        <div className="space-y-4">
          <p className="text-xs text-ink-500">
            {current.kind === "main" ? `MAIN QUESTION ${current.main_number} OF ${mainLimit}` : `FOLLOW-UP · TOPIC ${current.main_number}`}
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={readAloud} onChange={(event) => setReadAloud(event.target.checked)} />
            Read questions aloud
          </label>
          <QuestionCard turn={current} readAloud={readAloud} />
          <Card>
            <h2 className="text-lg font-semibold">Your answer</h2>
            <p className="mt-1 text-xs text-ink-500">
              Let the question finish speaking before recording. Tap the microphone to start, then tap stop when finished.
            </p>
            {mics.length > 0 && (
              <div className="mt-3 max-w-md">
                <Field
                  label="Microphone"
                  hint="If recordings are silent, your browser may be listening to a loopback input. Pick your real mic here."
                >
                  <Select value={micId} onChange={(event) => setMicId(event.target.value)} disabled={recording || busy}>
                    <option value="">System default</option>
                    {mics.map((device, index) => (
                      <option key={device.deviceId} value={device.deviceId} disabled={isLoopbackInput(device.label)}>
                        {microphoneLabel(device, index)}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button variant={recording ? "danger" : "secondary"} onClick={() => void toggleRecording()} disabled={busy}>
                {recording ? "■ Stop recording" : "🎙 Record your answer"}
              </Button>
              {recorded && !recording && (
                <span className="text-sm text-ink-500">
                  Recording ready{recordSecs != null ? ` (${recordSecs}s)` : ""}. Play it back, then submit.
                </span>
              )}
              <Button loading={busy} disabled={!recorded || recording} onClick={() => void runAction(recorded, current.turn_id)}>
                Submit answer
              </Button>
            </div>
            {recording && (
              <div className="mt-3">
                <div className="h-2 overflow-hidden rounded-full bg-slate-200" role="meter" aria-label="Microphone input level">
                  <div
                    className="h-full rounded-full bg-brand-600"
                    style={{ width: `${Math.round(Math.min(1, level * 6) * 100)}%` }}
                  />
                </div>
                <p className="mt-1 text-xs text-ink-500">
                  {level < 0.01
                    ? "No input detected — speak and watch this bar. If it stays flat, stop and pick another microphone above."
                    : "Input detected — good, keep talking."}
                </p>
              </div>
            )}
            {recordUrl && !recording && <audio className="mt-3 w-full" controls src={recordUrl} />}
            {recordError && (
              <div className="mt-3">
                <Alert tone="error">{recordError}</Alert>
              </div>
            )}
          </Card>
        </div>
      ) : (
        <div className="space-y-4">
          <Card>
            <p className="text-xs text-ink-500">YOUR ANSWER</p>
            <p className="mt-1 whitespace-pre-wrap text-sm">{current.answer_text}</p>
          </Card>
          <Button loading={busy} onClick={() => void runAction(null, null)}>
            Continue interview
          </Button>
        </div>
      )}
      {answered.length > 0 && (
        <div className="mt-6 space-y-3">
          {current && current.answer_text == null && (
            <Card>
              <p className="text-xs text-ink-500">YOUR LAST ANSWER</p>
              <p className="mt-1 whitespace-pre-wrap text-sm">{answered[answered.length - 1].answer_text}</p>
            </Card>
          )}
          <details className="rounded-xl border border-slate-200 bg-white p-4">
            <summary className="cursor-pointer text-sm font-medium">Conversation so far</summary>
            <div className="mt-2 space-y-4">
              {answered.map((turn) => (
                <div key={turn.turn_id}>
                  <p className="font-semibold">Question {turn.sequence}</p>
                  <p className="whitespace-pre-wrap text-sm">{turn.question}</p>
                  <p className="mt-1 text-xs text-ink-500">YOUR ANSWER</p>
                  <p className="whitespace-pre-wrap text-sm">{turn.answer_text}</p>
                </div>
              ))}
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
