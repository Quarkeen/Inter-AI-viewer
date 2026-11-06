// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { watchGaze } from './gazeCamera';
import { gazeFeatures } from './gaze';

vi.mock('./gaze', async original => ({ ...await original<typeof import('./gaze')>(), gazeFeatures: vi.fn() }));
const usable = { features: [.02, .03, .01, .2], center: { x: .5, y: .3 }, size: .25 };
let workers: FakeWorker[];
class FakeWorker {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { workers.push(this); }
  emit(kind: string) {
    const request = this.postMessage.mock.calls.slice().reverse().find(([message]) => message.kind === 'frame')?.[0];
    this.onmessage?.({ data: { kind, faces: [[]], aspect: 4 / 3, at: request?.at } } as MessageEvent);
  }
}
beforeEach(() => {
  workers = []; vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ['performance', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] });
  vi.stubGlobal('Worker', FakeWorker);
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ close: vi.fn() })));
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  vi.mocked(gazeFeatures).mockReturnValue(usable);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function start() {
  const video = { readyState: 4, paused: false, currentTime: 0 };
  const reading = vi.fn(), error = vi.fn();
  const stop = watchGaze(video as HTMLVideoElement, reading, error);
  return { video, reading, error, stop };
}
it.each(['hidden', 'paused', 'not-ready'])('resumes after %s video for more than five seconds without killing the worker', async condition => {
  const { video, reading, error, stop } = start();
  const worker = workers[0]; worker.emit('ready');
  if (condition === 'hidden') Object.defineProperty(document, 'hidden', { configurable: true, value: true });
  else if (condition === 'paused') video.paused = true;
  else video.readyState = 1;
  await vi.advanceTimersByTimeAsync(6500);
  expect(worker.terminate).not.toHaveBeenCalled(); expect(error).not.toHaveBeenCalled();
  Object.defineProperty(document, 'hidden', { configurable: true, value: false }); video.paused = false; video.readyState = 4; video.currentTime++;
  await vi.advanceTimersByTimeAsync(125); worker.emit('reading');
  expect(reading).toHaveBeenLastCalledWith(usable); expect(workers).toHaveLength(1);
  stop();
});
it('recovers fresh frames after a stalled camera without reinitializing its model', async () => {
  const { video, reading, error, stop } = start();
  const worker = workers[0]; worker.emit('ready');
  await vi.advanceTimersByTimeAsync(125); worker.emit('reading');
  await vi.advanceTimersByTimeAsync(6500);
  expect(reading).toHaveBeenLastCalledWith(null); expect(error).not.toHaveBeenCalled();
  video.currentTime++; await vi.advanceTimersByTimeAsync(125); worker.emit('reading');
  expect(reading).toHaveBeenLastCalledWith(usable); expect(worker.terminate).not.toHaveBeenCalled();
  stop();
});
it('restarts a hung worker, discards late replies and resumes usable readings', async () => {
  const { reading, error, stop } = start();
  const first = workers[0]; first.emit('ready');
  await vi.advanceTimersByTimeAsync(5250);
  expect(error).toHaveBeenCalledOnce(); expect(first.terminate).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(1000);
  expect(workers).toHaveLength(2);
  reading.mockClear(); first.emit('reading'); expect(reading).not.toHaveBeenCalled();
  workers[1].emit('ready'); await vi.advanceTimersByTimeAsync(125); workers[1].emit('reading');
  expect(reading).toHaveBeenLastCalledWith(usable);
  stop();
});
it('retries a worker error automatically and cancels retries on cleanup', async () => {
  const { error, stop } = start();
  workers[0].onerror?.(); expect(error).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(1000); expect(workers).toHaveLength(2);
  workers[1].onerror?.(); stop();
  await vi.advanceTimersByTimeAsync(30_000); expect(workers).toHaveLength(2);
});
it('closes a pending image bitmap after unmount without posting it to a dead worker', async () => {
  let resolve!: (frame: ImageBitmap) => void;
  vi.mocked(createImageBitmap).mockImplementation(() => new Promise<ImageBitmap>(done => { resolve = done; }));
  const { stop } = start(); const worker = workers[0]; worker.emit('ready');
  await vi.advanceTimersByTimeAsync(125); stop();
  const frame = { close: vi.fn() } as unknown as ImageBitmap;
  resolve(frame); await vi.advanceTimersByTimeAsync(0);
  expect(frame.close).toHaveBeenCalledOnce();
  expect(worker.postMessage.mock.calls.filter(([message]) => message.kind === 'frame')).toHaveLength(0);
});
