// @vitest-environment jsdom
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Interview } from "./Interview";
import { api, type InterviewState } from "../lib/api";
import { fitGaze, validateGaze, GAZE_TARGETS, GAZE_VALIDATION, type GazeModel } from '../lib/gaze';

function calibrationFixture() {
  const samples = (targets: typeof GAZE_TARGETS) => targets.flatMap(target => Array.from({ length: 12 }, () => ({ target, reading: { features: [target.x * .12, target.y * .12, .02, .2], center: { x: .5, y: .3 }, size: .25 } })));
  return validateGaze(fitGaze(samples(GAZE_TARGETS), { width: innerWidth, height: innerHeight, camera: 'test-camera' }), samples(GAZE_VALIDATION));
}

const lifecycle = vi.hoisted(() => ({ unmount: vi.fn(), autoCalibration: true }));
vi.mock('../components/GazeCalibration', () => ({
  GazeCalibration: ({ onComplete }: { onComplete: (model: GazeModel) => void }) => {
    useEffect(() => { if (lifecycle.autoCalibration) onComplete(calibrationFixture()); }, []);
    return <div>Calibration fixture<button onClick={() => onComplete(calibrationFixture())}>Pass calibration fixture</button><button onClick={() => onComplete(null as unknown as GazeModel)}>Invalid calibration fixture</button></div>;
  },
}));
vi.mock("../components/InterviewSetup", () => ({
  InterviewSetup: ({ onReady, onContinue }: { onReady: (receipt: string | null, mic: string) => void; onContinue: () => void }) => {
    useEffect(() => () => lifecycle.unmount(), []);
    return <div>
      <button onClick={() => onReady("verified-receipt", "tested-mic")}>Pass setup fixture</button>
      <button onClick={() => onReady(null, "tested-mic")}>Invalidate setup fixture</button>
      <button onClick={onContinue}>Continue fixture</button>
    </div>;
  },
}));
vi.mock("../lib/audio", () => ({ listMicrophones: vi.fn(async () => []), collectRecording: vi.fn(), recordAnswerMic: vi.fn(), toWav16k: vi.fn(), watchMicLevel: vi.fn() }));
vi.mock("../components/InterviewCamera", () => ({
  InterviewCamera: ({ onEvent }: { onEvent: (event: { kind: string; detail: object; at: string }) => void }) => {
    const emitCamera = (state: string) => onEvent({ kind: "camera.state", detail: { state }, at: new Date().toISOString() });
    const emitFaces = (count: number) => onEvent({ kind: "face.count", detail: { count }, at: new Date().toISOString() });
    return <div data-testid="camera-monitor">
      <button onClick={() => emitCamera("ended")}>Drop camera fixture</button>
      <button onClick={() => emitCamera("started")}>Restore camera fixture</button>
      <button onClick={() => emitFaces(0)}>Face gone fixture</button>
      <button onClick={() => emitFaces(1)}>Face back fixture</button>
    </div>;
  },
}));
vi.mock("../lib/api", async importOriginal => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, api: { ...original.api, interviewState: vi.fn(), startSession: vi.fn(), advanceInterview: vi.fn(), reportEvents: vi.fn(), pauseInterview: vi.fn(), resumeInterview: vi.fn(), terminateInterview: vi.fn() } };
});
const available: InterviewState = { access: { state: "available", duration_minutes: 30, opens_at: "2030-01-01T10:00:00Z", closes_at: "2030-01-01T11:00:00Z" }, session: null, turns: [], completion_message: "Done" };
function open(query = "") { render(<MemoryRouter initialEntries={[`/interview?token=test-token${query}`]}><Interview /></MemoryRouter>); }
beforeEach(() => {
  vi.clearAllMocks();
  lifecycle.autoCalibration = true;
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  vi.mocked(api.interviewState).mockResolvedValue(available);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("candidate preparation screens", () => {
  it('shows viewing-area calibration after device verification and before instructions without starting the timer', async () => {
    lifecycle.autoCalibration = false;
    open();
    fireEvent.click(await screen.findByRole('button', { name: 'Pass setup fixture' }));
    await screen.findByRole('heading', { name: 'Viewing area calibration' });
    expect(screen.queryByRole('button', { name: /Start interview/ })).toBeNull();
    expect(api.startSession).not.toHaveBeenCalled();
    expect(lifecycle.unmount).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Invalid calibration fixture' }));
    expect(screen.queryByRole('button', { name: /Start interview/ })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Viewing area calibration' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Pass calibration fixture' }));
    await screen.findByRole('heading', { name: 'Interview instructions' });
    expect(screen.getByRole('button', { name: /Start interview/ })).toBeTruthy();
  });
  it("does not expose instructions or start controls before verification, even through a direct link", async () => {
    open("&step=instructions");
    await screen.findByRole("heading", { name: "System check" });
    expect(screen.queryByRole("button", { name: /Start interview/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "How to answer" })).toBeNull();
    expect(api.startSession).not.toHaveBeenCalled();
  });
  it("shows a separate instructions screen without unmounting live device checks or starting the timer", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Pass setup fixture" }));
    await screen.findByRole("heading", { name: "Interview instructions" });
    expect(screen.queryByRole("button", { name: "Pass setup fixture" })).toBeNull();
    expect(screen.getByRole("button", { name: /Start interview/ })).toBeDefined();
    expect(lifecycle.unmount).not.toHaveBeenCalled();
    expect(api.startSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Back to system check/ }));
    await screen.findByRole("heading", { name: "System check" });
    expect(screen.queryByRole("button", { name: /Start interview/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Continue fixture" }));
    await screen.findByRole("heading", { name: "Interview instructions" });
  });
  it("returns to setup when verification is invalidated", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Pass setup fixture" }));
    await screen.findByRole("heading", { name: "Interview instructions" });
    // Simulate a track ending or receipt expiry while its UI is hidden.
    fireEvent.click(screen.getByText("Invalidate setup fixture"));
    await screen.findByRole("heading", { name: "System check" });
    expect(screen.queryByRole("button", { name: /Start interview/ })).toBeNull();
  });
  it("reports browser events while the session is active", async () => {
    vi.mocked(api.startSession).mockResolvedValue({ session_id: "fixture" });
    vi.mocked(api.advanceInterview).mockResolvedValue({ ...available, session: { session_id: "fixture", status: "active", slot_end: "2030-01-01T11:00:00Z", paused_at: null, pause_reason: null, terminate_at: null, job_title: "Demo role", full_name: "Test candidate" } });
    vi.mocked(api.reportEvents).mockResolvedValue({ saved: 1 });
    const { unmount } = render(<MemoryRouter initialEntries={["/interview?token=test-token"]}><Interview /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "Pass setup fixture" }));
    fireEvent.click(await screen.findByRole("button", { name: /Start interview/ }));
    await screen.findByRole("heading", { name: "Demo role" });
    expect(screen.getByTestId("camera-monitor")).toBeDefined();
    window.dispatchEvent(new Event("blur"));
    unmount();
    await waitFor(() => expect(api.reportEvents).toHaveBeenCalledWith("test-token", expect.arrayContaining([
      expect.objectContaining({ kind: "browser.focus", detail: { focused: false } }),
    ])));
  });
  it("creates the session only after the explicit Start action and submits the verified receipt", async () => {
    vi.mocked(api.startSession).mockResolvedValue({ session_id: "fixture" });
    vi.mocked(api.advanceInterview).mockResolvedValue({ ...available, session: { session_id: "fixture", status: "active", slot_end: "2030-01-01T11:00:00Z", paused_at: null, pause_reason: null, terminate_at: null, job_title: "Demo role", full_name: "Test candidate" } });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Pass setup fixture" }));
    fireEvent.click(await screen.findByRole("button", { name: /Start interview/ }));
    await waitFor(() => expect(api.startSession).toHaveBeenCalledExactlyOnceWith("test-token", "verified-receipt"));
    await screen.findByRole("heading", { name: "Demo role" });
  });
});

describe("interview pause enforcement", () => {
  const live = {
    ...available,
    session: { session_id: "fixture", status: "active", slot_end: "2030-01-01T11:00:00Z", paused_at: null, pause_reason: null, terminate_at: null, job_title: "Demo role", full_name: "Test candidate" },
  } as InterviewState;
  function setFullscreen(enabled: boolean, element: object | null) {
    Object.defineProperty(document, "fullscreenEnabled", { value: enabled, configurable: true });
    Object.defineProperty(document, "fullscreenElement", { value: element, configurable: true });
  }

  it("confirms fullscreen at Start even when the browser does not dispatch a change event", async () => {
    setFullscreen(true, null);
    const request = vi.fn(async () => { setFullscreen(true, document.documentElement); });
    Object.defineProperty(document.documentElement, "requestFullscreen", { value: request, configurable: true });
    try {
      vi.mocked(api.startSession).mockImplementation(async () => {
        expect(document.fullscreenElement).toBe(document.documentElement);
        return { session_id: "fixture" };
      });
      vi.mocked(api.advanceInterview).mockResolvedValue(live);
      open();
      fireEvent.click(await screen.findByRole("button", { name: "Pass setup fixture" }));
      fireEvent.click(await screen.findByRole("button", { name: /Start interview/ }));
      await screen.findByRole("heading", { name: "Demo role" });
      expect(request).toHaveBeenCalledOnce();
      expect(screen.queryByText(/You left fullscreen/)).toBeNull();
      expect(api.pauseInterview).not.toHaveBeenCalled();
    } finally {
      Reflect.deleteProperty(document.documentElement, "requestFullscreen");
      setFullscreen(undefined as unknown as boolean, null);
    }
  });
  it("does not start the session when the fullscreen request is denied", async () => {
    setFullscreen(true, null);
    Object.defineProperty(document.documentElement, "requestFullscreen", { value: vi.fn(async () => { throw new Error("Denied"); }), configurable: true });
    try {
      open();
      fireEvent.click(await screen.findByRole("button", { name: "Pass setup fixture" }));
      fireEvent.click(await screen.findByRole("button", { name: /Start interview/ }));
      await screen.findByText(/Allow fullscreen for this site/);
      expect(api.startSession).not.toHaveBeenCalled();
    } finally {
      Reflect.deleteProperty(document.documentElement, "requestFullscreen");
      setFullscreen(undefined as unknown as boolean, null);
    }
  });
  it("polls for a missed return event and gives each later exit a fresh grace period", async () => {
    setFullscreen(true, document.documentElement);
    let fakeNow = 3_000_000;
    const clock = vi.spyOn(Date, "now").mockImplementation(() => fakeNow);
    try {
      vi.mocked(api.interviewState).mockResolvedValue(live);
      open();
      await screen.findByRole("heading", { name: "Demo role" });
      expect(screen.queryByText(/You left fullscreen/)).toBeNull();
      setFullscreen(true, null);
      fireEvent(document, new Event("fullscreenchange"));
      await screen.findByText(/You left fullscreen/);
      fakeNow += 29_999;
      setFullscreen(true, document.documentElement); // return just before expiry, with no event
      fakeNow += 2;
      await waitFor(() => expect(screen.queryByText(/You left fullscreen/)).toBeNull());
      fakeNow += 60_000;
      await new Promise(resolve => setTimeout(resolve, 300));
      expect(api.pauseInterview).not.toHaveBeenCalled();
      setFullscreen(true, null);
      fireEvent(document, new Event("fullscreenchange"));
      await screen.findByText(/You left fullscreen/);
      expect(api.pauseInterview).not.toHaveBeenCalled();
      fakeNow += 5000;
      setFullscreen(true, document.documentElement);
      fireEvent(document, new Event("fullscreenchange"));
      await waitFor(() => expect(screen.queryByText(/You left fullscreen/)).toBeNull());
      expect(api.pauseInterview).not.toHaveBeenCalled();
      expect(api.resumeInterview).not.toHaveBeenCalled();
    } finally { clock.mockRestore(); setFullscreen(undefined as unknown as boolean, null); }
  });
  it("warns before pausing on fullscreen loss, then resumes once entered", async () => {
    setFullscreen(true, null);
    let fakeNow = 1_000_000;
    const clock = vi.spyOn(Date, "now").mockImplementation(() => fakeNow);
    try {
      vi.mocked(api.interviewState).mockResolvedValue(live);
      vi.mocked(api.pauseInterview).mockResolvedValue({ paused_at: "2030-01-01T10:00:00Z", pause_reason: "fullscreen-required", terminate_at: null });
      vi.mocked(api.resumeInterview).mockResolvedValue({ slot_end: "2030-01-01T11:00:00Z", credited_seconds: 0 });
      vi.mocked(api.reportEvents).mockResolvedValue({ saved: 1 });
      open();
      expect(await screen.findByText(/You left fullscreen/)).toBeDefined();
      expect(api.pauseInterview).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Prepare first question" })).toBeDefined();
      fakeNow += 31_000;
      expect(await screen.findByText("Enter fullscreen to begin")).toBeDefined();
      await waitFor(() => expect(api.pauseInterview).toHaveBeenCalledWith("test-token", "fullscreen-required"));
      setFullscreen(true, document.createElement("div"));
      fireEvent(document, new Event("fullscreenchange"));
      await waitFor(() => expect(api.resumeInterview).toHaveBeenCalledWith("test-token"));
      expect(await screen.findByRole("button", { name: "Prepare first question" })).toBeDefined();
      cleanup();
      await waitFor(() =>
        expect(api.reportEvents).toHaveBeenCalledWith(
          "test-token",
          expect.arrayContaining([expect.objectContaining({ kind: "pause.started" })]),
        ),
      );
    } finally {
      clock.mockRestore();
      setFullscreen(undefined as unknown as boolean, null);
    }
  });
  it("warns before pausing when the candidate leaves the frame", async () => {
    let fakeNow = 2_000_000;
    const clock = vi.spyOn(Date, "now").mockImplementation(() => fakeNow);
    try {
      vi.mocked(api.interviewState).mockResolvedValue(live);
      vi.mocked(api.pauseInterview).mockResolvedValue({ paused_at: "2030-01-01T10:00:00Z", pause_reason: "face-absent", terminate_at: null });
      vi.mocked(api.resumeInterview).mockResolvedValue({ slot_end: "2030-01-01T11:00:00Z", credited_seconds: 0 });
      open();
      await screen.findByRole("heading", { name: "Demo role" });
      fireEvent.click(screen.getByRole("button", { name: "Restore camera fixture" }));
      fireEvent.click(screen.getByRole("button", { name: "Face gone fixture" }));
      expect(await screen.findByText(/out of the camera frame/)).toBeDefined();
      expect(api.pauseInterview).not.toHaveBeenCalled();
      fakeNow += 31_000;
      expect(await screen.findByText("Interview paused")).toBeDefined();
      await waitFor(() => expect(api.pauseInterview).toHaveBeenCalledWith("test-token", "face-absent"));
      fireEvent.click(screen.getByRole("button", { name: "Face back fixture" }));
      await waitFor(() => expect(api.resumeInterview).toHaveBeenCalledWith("test-token"));
      expect(await screen.findByRole("button", { name: "Prepare first question" })).toBeDefined();
    } finally {
      clock.mockRestore();
    }
  });
  it("ends the interview when an armed countdown runs out", async () => {
    setFullscreen(true, document.createElement("div"));
    try {
      const paused = { ...live, session: { ...live.session!, paused_at: "2030-01-01T10:00:00Z", pause_reason: "fullscreen-exit", terminate_at: "2000-01-01T00:00:00Z" } };
      const ended = { ...live, session: { ...live.session!, status: "terminated", paused_at: "2030-01-01T10:00:00Z", pause_reason: "fullscreen-exit", terminate_at: "2000-01-01T00:00:00Z" } } as InterviewState;
      vi.mocked(api.interviewState).mockResolvedValueOnce(paused).mockResolvedValue(ended);
      vi.mocked(api.resumeInterview).mockResolvedValue({ slot_end: "2030-01-01T11:00:00Z", credited_seconds: 0 });
      vi.mocked(api.terminateInterview).mockResolvedValue({ terminated: true, remaining_seconds: 0 });
      open();
      expect(await screen.findByText(/Interview ends in/)).toBeDefined();
      await waitFor(() => expect(api.terminateInterview).toHaveBeenCalledWith("test-token"));
      expect(await screen.findByText("Your interview has ended")).toBeDefined();
    } finally {
      setFullscreen(undefined as unknown as boolean, null);
    }
  });
  it("resumes a server-paused session once its conditions are met", async () => {
    setFullscreen(true, document.createElement("div"));
    try {
      const paused = { ...live, session: { ...live.session!, paused_at: "2030-01-01T10:00:00Z", pause_reason: "fullscreen-exit" } };
      vi.mocked(api.interviewState).mockResolvedValueOnce(paused).mockResolvedValue(live);
      vi.mocked(api.resumeInterview).mockResolvedValue({ slot_end: "2030-01-01T11:05:00Z", credited_seconds: 300 });
      vi.mocked(api.reportEvents).mockResolvedValue({ saved: 1 });
      const { unmount } = render(<MemoryRouter initialEntries={["/interview?token=test-token"]}><Interview /></MemoryRouter>);
      expect(await screen.findByText("Interview paused")).toBeDefined();
      await waitFor(() => expect(api.resumeInterview).toHaveBeenCalledWith("test-token"));
      expect(await screen.findByRole("button", { name: "Prepare first question" })).toBeDefined();
      unmount();
      await waitFor(() =>
        expect(api.reportEvents).toHaveBeenCalledWith(
          "test-token",
          expect.arrayContaining([expect.objectContaining({ kind: "pause.ended" })]),
        ),
      );
    } finally {
      setFullscreen(undefined as unknown as boolean, null);
    }
  });
  it("pauses when the camera disconnects and resumes when it is restored", async () => {
    vi.mocked(api.interviewState).mockResolvedValue(live);
    vi.mocked(api.pauseInterview).mockResolvedValue({ paused_at: "2030-01-01T10:00:00Z", pause_reason: "camera-ended", terminate_at: null });
    vi.mocked(api.resumeInterview).mockResolvedValue({ slot_end: "2030-01-01T11:00:00Z", credited_seconds: 0 });
    open();
    await screen.findByRole("heading", { name: "Demo role" });
    fireEvent.click(screen.getByRole("button", { name: "Drop camera fixture" }));
    expect(await screen.findByText(/camera disconnected/i)).toBeDefined();
    expect(api.pauseInterview).toHaveBeenCalledWith("test-token", "camera-ended");
    expect(screen.queryByRole("button", { name: "Prepare first question" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Restore camera fixture" }));
    await waitFor(() => expect(api.resumeInterview).toHaveBeenCalledWith("test-token"));
    expect(await screen.findByRole("button", { name: "Prepare first question" })).toBeDefined();
  });
  it("lets the candidate continue without a camera as a reviewed override", async () => {
    vi.mocked(api.interviewState).mockResolvedValue(live);
    vi.mocked(api.pauseInterview).mockResolvedValue({ paused_at: "2030-01-01T10:00:00Z", pause_reason: "camera-ended", terminate_at: null });
    vi.mocked(api.resumeInterview).mockResolvedValue({ slot_end: "2030-01-01T11:00:00Z", credited_seconds: 0 });
    vi.mocked(api.reportEvents).mockResolvedValue({ saved: 1 });
    const { unmount } = render(<MemoryRouter initialEntries={["/interview?token=test-token"]}><Interview /></MemoryRouter>);
    await screen.findByRole("heading", { name: "Demo role" });
    fireEvent.click(screen.getByRole("button", { name: "Drop camera fixture" }));
    fireEvent.click(await screen.findByRole("button", { name: "Continue without camera" }));
    await waitFor(() => expect(api.resumeInterview).toHaveBeenCalledWith("test-token"));
    expect(api.resumeInterview).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Prepare first question" })).toBeDefined();
    unmount();
    await waitFor(() =>
      expect(api.reportEvents).toHaveBeenCalledWith(
        "test-token",
        expect.arrayContaining([expect.objectContaining({ kind: "pause.ended", detail: { reason: null, override: true } })]),
      ),
    );
  });
});

describe('server-owned warning termination', () => {
  it('shows the server termination reason after a warning acknowledgement', async () => {
    Object.defineProperty(document,'fullscreenEnabled',{value:true,configurable:true});
    Object.defineProperty(document,'fullscreenElement',{value:document.documentElement,configurable:true});
    const session={session_id:'fixture',status:'active' as const,slot_end:'2030-01-01T11:00:00Z',paused_at:null,pause_reason:null,terminate_at:null,job_title:'Demo role',full_name:'Test candidate'};
    const policy={warning_count:3,warning_limit:3,terminate_on_interruption:true,active_warnings:{},terminated:false};
    const ended={...available,session:{...session,status:'terminated' as const,pause_reason:'warning-limit'},proctoring:{...policy,warning_count:4,terminated:true}};
    vi.mocked(api.interviewState).mockResolvedValueOnce({...available,session,proctoring:policy}).mockResolvedValue(ended);
    vi.mocked(api.reportEvents).mockResolvedValue({saved:1,proctoring:ended.proctoring});
    open(); await screen.findByRole('heading',{name:'Demo role'});
    fireEvent.click(screen.getByRole('button',{name:'Restore camera fixture'}));
    fireEvent.click(screen.getByRole('button',{name:'Face gone fixture'}));
    expect(await screen.findByRole('heading',{name:'Your interview has ended'})).toBeTruthy();
    expect(screen.getByText(/allowed fullscreen\/face-absence warning count was exceeded/)).toBeTruthy();
    expect(screen.queryByRole('button',{name:'Prepare first question'})).toBeNull();
  });
});

it('retries a failed pause request instead of leaving termination permanently unarmed', async () => {
  Object.defineProperty(document,'fullscreenEnabled',{value:true,configurable:true});
  Object.defineProperty(document,'fullscreenElement',{value:null,configurable:true});
  let fakeNow=1_000_000;
  const clock=vi.spyOn(Date,'now').mockImplementation(()=>fakeNow);
  const live={...available,session:{session_id:'fixture',status:'active' as const,slot_end:'2030-01-01T11:00:00Z',paused_at:null,pause_reason:null,terminate_at:null,job_title:'Demo role',full_name:'Test candidate'}};
  vi.mocked(api.interviewState).mockResolvedValue(live);
  vi.mocked(api.reportEvents).mockResolvedValue({saved:1});
  vi.mocked(api.pauseInterview).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({paused_at:'2030-01-01T10:00:00Z',pause_reason:'fullscreen-required',terminate_at:null});
  try {
    open(); await screen.findByText(/You left fullscreen/);
    fakeNow+=31_000;
    await waitFor(()=>expect(api.pauseInterview).toHaveBeenCalledTimes(1));
    await waitFor(()=>expect(api.pauseInterview).toHaveBeenCalledTimes(2),{timeout:4200});
  } finally {
    cleanup(); clock.mockRestore();
    Object.defineProperty(document,'fullscreenEnabled',{value:undefined,configurable:true});
  }
},6000);
