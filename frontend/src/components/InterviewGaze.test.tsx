// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { InterviewGaze } from './InterviewGaze';
import { watchGaze } from '../lib/gazeCamera';
import { fitGaze, GAZE_TARGETS, GAZE_VALIDATION, validateGaze, type GazeReading } from '../lib/gaze';

vi.mock('../lib/gazeCamera', () => ({ cameraDevice: () => 'test-camera', watchGaze: vi.fn() }));
let emit: (reading: GazeReading | null) => void;
let fail: () => void;
const stop = vi.fn();
const reading = (x: number, y: number): GazeReading => ({ features: [x * .12, y * .12, .02, .2], center: { x: .5, y: .3 }, size: .25 });
function model() {
  const sample = (points: typeof GAZE_TARGETS) => points.flatMap(target => Array.from({ length: 12 }, () => ({ target, reading: reading(target.x, target.y) })));
  return validateGaze(fitGaze(sample(GAZE_TARGETS), { width: innerWidth, height: innerHeight, camera: 'test-camera' }), sample(GAZE_VALIDATION));
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'performance', 'setInterval', 'clearInterval'] }); vi.clearAllMocks();
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: false });
  vi.mocked(watchGaze).mockImplementation((_video, callback, onError) => { emit = callback; fail = onError; return stop; });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
it('emits review-only warning evidence after sustained look-away and clears after a stable return', () => {
  const event = vi.fn();
  const { unmount } = render(<InterviewGaze video={{} as HTMLVideoElement} model={model()} active onEvent={event} />);
  for (let i = 0; i < 21; i++) act(() => { vi.advanceTimersByTime(250); emit(reading(-.8, .5)); });
  expect(event.mock.calls.filter(([e]) => e.kind === 'warn.started')).toHaveLength(0);
  for (let i = 0; i < 21; i++) act(() => { vi.advanceTimersByTime(250); emit(reading(-.8, .5)); });
  expect(event.mock.calls.filter(([e]) => e.kind === 'warn.started')).toHaveLength(1);
  expect(event).toHaveBeenCalledWith(expect.objectContaining({ kind: 'warn.started', detail: expect.objectContaining({ signal: 'gaze', review_only: true, grace_seconds: 5 }) }));
  for (let i = 0; i < 5; i++) act(() => { vi.advanceTimersByTime(250); emit(reading(.5, .5)); });
  expect(event).toHaveBeenCalledWith(expect.objectContaining({ kind: 'warn.cleared' }));
  unmount(); expect(stop).toHaveBeenCalled();
});
it('returns to Active after tracking recovers and reports recovery without requiring recalibration', () => {
  const event = vi.fn();
  render(<InterviewGaze video={{} as HTMLVideoElement} model={model()} active onEvent={event} />);
  act(() => { emit(reading(.5, .5)); });
  expect(screen.getByRole('status', { name: 'Eye tracking active' })).toBeTruthy();
  act(() => { fail(); });
  expect(screen.getByRole('status', { name: 'Eye tracking not active' }).title).toMatch(/Reconnecting automatically/);
  act(() => { vi.advanceTimersByTime(2000); emit(reading(.5, .5)); });
  expect(screen.getByRole('status', { name: 'Eye tracking active' })).toBeTruthy();
  expect(event.mock.calls.filter(([e]) => e.kind === 'gaze.state' && e.detail.state === 'enabled')).toHaveLength(2);
  expect(event.mock.calls.some(([e]) => e.kind.startsWith('warn.'))).toBe(false);
});
it('explains a changed head position in the badge and recovers when the original position returns', () => {
  render(<InterviewGaze video={{} as HTMLVideoElement} model={model()} active onEvent={() => {}} />);
  act(() => { emit({ ...reading(.5, .5), features: [.06, .06, .4, .5] }); });
  expect(screen.getByRole('status', { name: 'Eye tracking not active' }).title).toMatch(/head position differs/);
  act(() => { emit(reading(.5, .5)); });
  expect(screen.getByRole('status', { name: 'Eye tracking active' })).toBeTruthy();
});
it('records tracking gaps without warning for unknown eyes or a missing calibration', () => {
  const event = vi.fn();
  const { rerender } = render(<InterviewGaze video={{} as HTMLVideoElement} model={model()} active onEvent={event} />);
  for (let i = 0; i < 60; i++) act(() => { vi.advanceTimersByTime(250); emit(null); });
  expect(event.mock.calls.some(([e]) => e.kind.startsWith('warn.'))).toBe(false);
  expect(event).toHaveBeenCalledWith(expect.objectContaining({ kind: 'gaze.state', detail: expect.objectContaining({ state: 'uncertain' }) }));
  rerender(<InterviewGaze video={{} as HTMLVideoElement} model={null} active onEvent={event} />);
  expect(screen.getByRole('status', { name: 'Eye tracking not active' }).textContent).toBe('Not active');
  expect(stop).toHaveBeenCalled();
});
it('uses a fixed-size badge, hides routine explanations and ignores brief uncertainty in the indicator', () => {
  render(<InterviewGaze video={{} as HTMLVideoElement} model={model()} active onEvent={() => {}} />);
  expect(screen.getByRole('status', { name: 'Eye tracking not active' }).className).toContain('w-24');
  act(() => { emit(reading(.5, .5)); });
  expect(screen.getByRole('status', { name: 'Eye tracking active' }).textContent).toBe('Active');
  act(() => { vi.advanceTimersByTime(250); emit(null); vi.advanceTimersByTime(500); });
  expect(screen.getByRole('status', { name: 'Eye tracking active' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText(/Eye tracking is uncertain|Brief look-away allowance|Calibrated viewing area is active/)).toBeNull();
  act(() => { vi.advanceTimersByTime(1500); });
  expect(screen.getByRole('status', { name: 'Eye tracking not active' }).className).toContain('w-24');
});
