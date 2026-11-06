// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GazeCalibration } from './GazeCalibration';
import { watchGaze } from '../lib/gazeCamera';
import { GAZE_TARGETS, GAZE_VALIDATION, type GazeReading } from '../lib/gaze';

vi.mock('../lib/gazeCamera', () => ({ cameraDevice: () => 'test-camera', watchGaze: vi.fn() }));
let emit: (reading: GazeReading | null) => void;
const stop = vi.fn();
const video = { readyState: 4 } as HTMLVideoElement;
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date', 'performance'] });
  vi.clearAllMocks();
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: false });
  vi.mocked(watchGaze).mockImplementation((_video, callback) => { emit = callback; return stop; });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
it('collects nine calibration targets, validates new samples, then releases the local tracker', () => {
  const complete = vi.fn();
  render(<GazeCalibration video={video} onComplete={complete} onBack={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Begin calibration' }));
  for (const target of [...GAZE_TARGETS, ...GAZE_VALIDATION]) {
    act(() => { vi.advanceTimersByTime(750); });
    for (let i = 0; i < 12; i++) act(() => {
      emit({ features: [target.x * .12, target.y * .12, .02, .2], center: { x: .5, y: .3 }, size: .25 });
      vi.advanceTimersByTime(125);
    });
  }
  expect(complete).toHaveBeenCalledOnce();
  expect(complete.mock.calls[0][0]).toMatchObject({ camera: 'test-camera', margin: .18 });
  expect(complete.mock.calls[0][0].error).toBeLessThan(.05);
  expect(stop).toHaveBeenCalled();
});
it('keeps failed calibration blocked and offers retry without a skip path', () => {
  const complete = vi.fn();
  render(<GazeCalibration video={video} onComplete={complete} onBack={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Begin calibration' }));
  act(() => { vi.advanceTimersByTime(100_001); });
  expect(screen.getByText(/Calibration timed out/)).toBeTruthy();
  expect(complete).not.toHaveBeenCalled();
  expect(stop).toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: /Continue with standard monitoring/ })).toBeNull();
  expect(screen.getByRole('button', { name: 'Retry calibration' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Back to system check' })).toBeTruthy();
  expect(complete).not.toHaveBeenCalled();
});
it('does not start tracking when the setup camera is unavailable', () => {
  render(<GazeCalibration video={null} onComplete={() => {}} onBack={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Begin calibration' }));
  expect(screen.getByText(/Reconnect your camera in the system check/)).toBeTruthy();
  expect(watchGaze).not.toHaveBeenCalled();
});
