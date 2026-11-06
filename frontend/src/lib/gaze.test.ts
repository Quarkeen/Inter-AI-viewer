import { describe, expect, it } from 'vitest';
import { classifyGaze, fitGaze, gazeFeatures, GazeEpisodes, GAZE_TARGETS, GAZE_VALIDATION, predictGaze, validateGaze, type GazeReading, type GazeSample, type Point } from './gaze';

const reading = (x: number, y: number): GazeReading => ({ features: [x * .12, y * .12, .02, .2], center: { x: .5, y: .3 }, size: .25 });
const samples = (points: Point[]): GazeSample[] => points.flatMap(target => Array.from({ length: 12 }, () => ({ target, reading: reading(target.x, target.y) })));
function calibrated() { return validateGaze(fitGaze(samples(GAZE_TARGETS), { width: 1200, height: 800, camera: 'fixture' }), samples(GAZE_VALIDATION)); }
describe('personal viewing area', () => {
  it('fits a mapping and validates it against separate samples', () => {
    const model = calibrated(), point = predictGaze(model, reading(.75, .25));
    expect(point.x).toBeCloseTo(.75, 1); expect(point.y).toBeCloseTo(.25, 1);
    expect(model.error).toBeLessThan(.05);
  });
  it('rejects calibration explained only by head movement instead of eye movement', () => {
    const headDriven = samples(GAZE_TARGETS).map(s => ({ ...s, reading: { ...s.reading, features: [.02, .02, (s.target.x - .5) * .1, .2 + (s.target.y - .5) * .1] } }));
    expect(() => fitGaze(headDriven, { width: 1200, height: 800, camera: 'fixture' })).toThrow(/clear eye movement/);
  });
  it('does not use correlated head pose to predict gaze and suspends after a substantial head turn', () => {
    const training = samples(GAZE_TARGETS).map(s => ({ ...s, reading: { ...s.reading, features: [...s.reading.features.slice(0, 2), .02 + (s.target.x - .5) * .1, .2 + (s.target.y - .5) * .1] } }));
    const model = validateGaze(fitGaze(training, { width: 1200, height: 800, camera: 'fixture' }), samples(GAZE_VALIDATION));
    const fixedEyes = reading(.5, .5), movedHead = { ...fixedEyes, features: [...fixedEyes.features.slice(0, 2), .1, .28] };
    expect(predictGaze(model, movedHead)).toEqual(predictGaze(model, fixedEyes));
    expect(classifyGaze(model, movedHead, { width: 1200, height: 800, camera: 'fixture' })).toBe('inside');
    expect(classifyGaze(model, { ...fixedEyes, features: [...fixedEyes.features.slice(0, 2), .3, .5] }, { width: 1200, height: 800, camera: 'fixture' })).toBe('unknown');
    expect(classifyGaze(model, reading(-.5, .5), { width: 1200, height: 800, camera: 'fixture' })).toBe('outside');
    const headTurned = samples(GAZE_VALIDATION).map(s => ({ ...s, reading: { ...s.reading, features: [...s.reading.features.slice(0, 2), .4, .5] } }));
    expect(() => validateGaze(model, headTurned)).toThrow(/head position changed/);
  });
  it('rejects incomplete and inaccurate calibration rather than inventing precision', () => {
    expect(() => fitGaze(samples(GAZE_TARGETS.slice(1)), { width: 1200, height: 800, camera: '' })).toThrow();
    const model = calibrated();
    expect(() => validateGaze(model, samples(GAZE_VALIDATION).map(s => ({ ...s, reading: reading(.5, .5) })))).toThrow(/not reliable/);
    expect(() => validateGaze(model, [])).toThrow(/incomplete/);
  });
  it('allows screen edges and a buffer, and suspends after camera or geometry changes', () => {
    const model = calibrated(), viewport = { width: 1200, height: 800, camera: 'fixture' };
    expect(classifyGaze(model, reading(-.1, 1.1), viewport)).toBe('inside');
    expect(classifyGaze(model, reading(-.5, .5), viewport)).toBe('outside');
    expect(classifyGaze(model, null, viewport)).toBe('unknown');
    expect(classifyGaze(model, reading(-.5, .5), { ...viewport, camera: 'different' })).toBe('unknown');
    expect(classifyGaze(model, reading(-.5, .5), { ...viewport, width: 800 })).toBe('unknown');
    expect(classifyGaze(model, { ...reading(.5, .5), size: .1 }, viewport)).toBe('unknown');
  });
  it('does not interpret missing eyes, multiple faces or blinks as look-away', () => {
    expect(gazeFeatures([])).toBeNull(); expect(gazeFeatures([[], []])).toBeNull();
    const face = Array.from({ length: 478 }, () => ({ x: .5, y: .5 }));
    expect(gazeFeatures([face])).toBeNull();
    face[33] = { x: .3, y: .3 }; face[133] = { x: .4, y: .3 };
    face[362] = { x: .6, y: .3 }; face[263] = { x: .7, y: .3 };
    face[159] = { x: .35, y: .28 }; face[145] = { x: .35, y: .32 };
    face[386] = { x: .65, y: .28 }; face[374] = { x: .65, y: .32 };
    face[468] = { x: .35, y: .3 }; face[473] = { x: .65, y: .3 }; face[1] = { x: .5, y: .4 };
    expect(gazeFeatures([face])).not.toBeNull();
    face[159] = face[145]; expect(gazeFeatures([face])).toBeNull();
  });
});
describe('tolerant warning episodes', () => {
  it('ignores shorter glances and warns once only after five seconds of valid look-away', () => {
    const monitor = new GazeEpisodes();
    for (let at = 0; at <= 3000; at += 250) expect(monitor.update('outside', at)).toBeNull();
    expect(monitor.update('inside', 3250)).toBeNull();
    for (let at = 3500; at <= 8500; at += 250) expect(monitor.update('outside', at)).toBeNull();
    expect(monitor.update('outside', 8750)).toBe('started');
    for (let at = 9000; at <= 12_000; at += 250) expect(monitor.update('outside', at)).toBeNull();
    expect(monitor.update('inside', 12_250)).toBeNull();
    for (let at = 12_500; at <= 13_000; at += 250) expect(monitor.update('inside', at)).toBeNull();
    expect(monitor.update('inside', 13_250)).toBe('cleared');
  });
  it('freezes for brief blinks but never accumulates long unknown or stale periods', () => {
    const monitor = new GazeEpisodes();
    for (let at = 0; at <= 4000; at += 250) monitor.update('outside', at);
    monitor.update('unknown', 4250); monitor.update('outside', 4500);
    let result: string | null = null;
    for (let at = 4750; at <= 5750; at += 250) result = monitor.update('outside', at) ?? result;
    expect(result).toBe('started');
    monitor.reset();
    for (let at = 0; at <= 4000; at += 250) monitor.update('outside', at);
    for (let at = 4250; at <= 12_000; at += 250) expect(monitor.update('unknown', at)).toBeNull();
    for (let at = 12_250; at <= 16_000; at += 250) expect(monitor.update('outside', at)).toBeNull();
    expect(monitor.update('outside', 60_000)).toBeNull();
  });
});
