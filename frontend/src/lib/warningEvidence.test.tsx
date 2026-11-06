// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { api, ApiError } from './api';
import { useMonitorQueue, type MonitorEvent } from './monitor';
import { captureWarningSnapshot } from './warningSnapshot';

vi.mock('./api', async original => {
  const actual = await original<typeof import('./api')>();
  return {...actual, api: {...actual.api, reportEvents: vi.fn()}};
});
const warning = (kind = 'warn.started'): MonitorEvent => ({kind, at: '2030-01-01T10:00:00Z', detail: {signal: 'face'}});
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); vi.mocked(api.reportEvents).mockResolvedValue({saved: 1}); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('retries warning evidence with its original ID and capture rather than taking a later frame', async () => {
  vi.mocked(api.reportEvents).mockRejectedValueOnce(new Error('offline'));
  const capture = vi.fn(() => ({data: 'data:image/jpeg;base64,FIRST', at: '2030-01-01T10:00:00Z'}));
  const synced = vi.fn();
  const {result} = renderHook(() => useMonitorQueue('token','session',{capture,onWarningsSynced:synced}));
  await act(async () => { result.current(warning()); });
  const first = vi.mocked(api.reportEvents).mock.calls[0][1][0] as MonitorEvent;
  capture.mockReturnValue({data: 'data:image/jpeg;base64,LATER',at:'2030-01-01T10:00:05Z'});
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  const retried = vi.mocked(api.reportEvents).mock.calls[1][1][0] as MonitorEvent;
  expect(retried.id).toBe(first.id);
  expect(retried.detail.warning_id).toBe(first.detail.warning_id);
  expect(retried.snapshot).toBe('data:image/jpeg;base64,FIRST');
  expect(capture).toHaveBeenCalledTimes(1);
  expect(synced).toHaveBeenCalledTimes(1);
});

it('recovers from a damaged snapshot by retrying the warning metadata', async () => {
  vi.mocked(api.reportEvents).mockRejectedValueOnce(new ApiError(400,'Invalid warning snapshot.'));
  const {result} = renderHook(() => useMonitorQueue('token','session',{capture: () => ({data:'data:image/jpeg;base64,BAD',at:'2030-01-01T10:00:00Z'})}));
  await act(async () => { result.current(warning()); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  const event = vi.mocked(api.reportEvents).mock.calls[1][1][0] as MonitorEvent;
  expect(event.snapshot).toBeUndefined(); expect(event.detail.warning_id).toBeTruthy();
});

it('reuses the server’s open episode after refresh and starts a fresh one only after recovery', async () => {
  const {result} = renderHook(() => useMonitorQueue('token','session',{activeWarnings:{face:'existing-episode'}}));
  await act(async () => { result.current(warning()); });
  expect(vi.mocked(api.reportEvents).mock.calls[0][1][0].detail).toMatchObject({warning_id:'existing-episode'});
  await act(async () => { result.current(warning('warn.cleared')); });
  expect(vi.mocked(api.reportEvents).mock.calls[1][1][0].detail).toMatchObject({warning_id:'existing-episode'});
  await act(async () => { result.current(warning()); });
  expect((vi.mocked(api.reportEvents).mock.calls[2][1][0].detail as any).warning_id).not.toBe('existing-episode');
});

it('does not substitute a disconnected or unready camera’s old frame', () => {
  const video = document.createElement('video');
  Object.defineProperties(video,{readyState:{value:2},videoWidth:{value:640},videoHeight:{value:480},srcObject:{value:{getVideoTracks:()=>[{readyState:'ended'}]}}});
  expect(captureWarningSnapshot(video)).toBeNull();
  expect(captureWarningSnapshot(null)).toBeNull();
});
