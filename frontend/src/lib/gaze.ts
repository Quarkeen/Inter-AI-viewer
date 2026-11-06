/** Calibrated webcam gaze estimates are review signals, never cheating verdicts. */
export type Point = { x: number; y: number };
export type Landmark = Point & { z?: number };
export type GazeReading = { features: number[]; center: Point; size: number };
export type GazeTarget = Point & { name: string };
export const GAZE_TARGETS: GazeTarget[] = [
  { x: .5, y: .5, name: 'center' }, { x: .15, y: .15, name: 'top left' },
  { x: .5, y: .15, name: 'top center' }, { x: .85, y: .15, name: 'top right' },
  { x: .85, y: .5, name: 'right' }, { x: .85, y: .85, name: 'bottom right' },
  { x: .5, y: .85, name: 'bottom center' }, { x: .15, y: .85, name: 'bottom left' },
  { x: .15, y: .5, name: 'left' },
];
export const GAZE_VALIDATION: GazeTarget[] = [
  { x: .28, y: .28, name: 'upper left check' }, { x: .72, y: .28, name: 'upper right check' },
  { x: .5, y: .5, name: 'center check' }, { x: .28, y: .72, name: 'lower left check' },
  { x: .72, y: .72, name: 'lower right check' },
];
export const GAZE_AWAY_MS = 5_000;
export const GAZE_RETURN_MS = 1_000;
export const GAZE_MAX_GAP_MS = 800;
export interface GazeSample { reading: GazeReading; target: Point }
export interface GazeModel {
  version: 2;
  mean: number[]; scale: number[]; x: number[]; y: number[];
  head: { yaw: number; pitch: number };
  center: Point; size: number; error: number; margin: number;
  width: number; height: number; camera: string; createdAt: number;
}

/** Eye-relative iris displacement plus head orientation proxies. Blinks,
 * missing irises, multiple faces and extreme rotations produce unknown readings. */
export function gazeFeatures(faces: Landmark[][], aspect = 4 / 3): GazeReading | null {
  if (faces.length !== 1 || faces[0].length < 478) return null;
  const points = faces[0].map(p => ({ x: p.x, y: p.y / aspect }));
  if (points.some(p => !Number.isFinite(p.x) || !Number.isFinite(p.y))) return null;
  function eye(a: number, b: number, top: number, bottom: number, iris: number) {
    const left = points[a], right = points[b];
    const dx = right.x - left.x, dy = right.y - left.y, width = Math.hypot(dx, dy);
    if (width < .015 || Math.hypot(points[top].x - points[bottom].x, points[top].y - points[bottom].y) / width < .12) return null;
    const ix = points[iris].x - (left.x + right.x) / 2, iy = points[iris].y - (left.y + right.y) / 2;
    return [(ix * dx + iy * dy) / (width * width), (iy * dx - ix * dy) / (width * width)];
  }
  const left = eye(33, 133, 159, 145, 468), right = eye(362, 263, 386, 374, 473);
  if (!left || !right || [...left, ...right].some(v => Math.abs(v) > .65) || Math.abs(left[0] - right[0]) > .25 || Math.abs(left[1] - right[1]) > .2) return null;
  const center = { x: (points[33].x + points[263].x) / 2, y: (points[33].y + points[263].y) / 2 };
  const size = Math.hypot(points[33].x - points[263].x, points[33].y - points[263].y);
  if (size < .08) return null;
  const yaw = (points[1].x - center.x) / size, pitch = (points[1].y - center.y) / size;
  if (Math.abs(yaw) > .6 || Math.abs(pitch) > .8) return null;
  return { features: [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2, yaw, pitch], center, size };
}

function solve(matrix: number[][], rhs: number[]): number[] {
  const a = matrix.map((row, i) => [...row, rhs[i]]), n = rhs.length;
  for (let i = 0; i < n; i++) {
    let pivot = i;
    for (let j = i + 1; j < n; j++) if (Math.abs(a[j][i]) > Math.abs(a[pivot][i])) pivot = j;
    [a[i], a[pivot]] = [a[pivot], a[i]];
    if (Math.abs(a[i][i]) < 1e-9) throw new Error('Calibration needs more eye movement. Please try again.');
    const divisor = a[i][i];
    for (let k = i; k <= n; k++) a[i][k] /= divisor;
    for (let j = 0; j < n; j++) if (j !== i) {
      const factor = a[j][i];
      for (let k = i; k <= n; k++) a[j][k] -= factor * a[i][k];
    }
  }
  return a.map(row => row[n]);
}
export function fitGaze(samples: GazeSample[], viewport: { width: number; height: number; camera: string }): GazeModel {
  if (samples.length < GAZE_TARGETS.length * 8 || samples.some(s => s.reading.features.length !== 4 || [...s.reading.features, s.reading.size, s.reading.center.x, s.reading.center.y, s.target.x, s.target.y].some(v => !Number.isFinite(v)))) throw new Error('Not enough usable calibration samples. Please retry.');
  if (GAZE_TARGETS.some(t => samples.filter(s => s.target.x === t.x && s.target.y === t.y).length < 8)) throw new Error('Look at every calibration point before continuing.');
  // Fit only eye-relative iris motion. Head pose must never substitute for it.
  const groups = GAZE_TARGETS.map(t => samples.filter(s => s.target.x === t.x && s.target.y === t.y));
  for (const axis of [0, 1]) {
    const means = groups.map(group => group.reduce((sum, s) => sum + s.reading.features[axis], 0) / group.length);
    const noise = Math.sqrt(groups.reduce((total, group, i) => total + group.reduce((sum, s) => sum + (s.reading.features[axis] - means[i]) ** 2, 0) / group.length, 0) / groups.length);
    if (Math.max(...means) - Math.min(...means) < Math.max(axis === 0 ? .025 : .015, noise * 3)) throw new Error('Not enough clear eye movement was detected. Keep your head still and follow the dots with your eyes, then retry.');
  }
  const pose = [2, 3].map(i => samples.map(s => s.reading.features[i]));
  if (pose.some(values => Math.max(...values) - Math.min(...values) > .12)) throw new Error('Your head moved too much during calibration. Keep it comfortably still and follow the dots with your eyes.');
  const mean = [0, 1].map(i => samples.reduce((sum, s) => sum + s.reading.features[i], 0) / samples.length);
  const scale = mean.map((m, i) => Math.max(.015, Math.sqrt(samples.reduce((sum, s) => sum + (s.reading.features[i] - m) ** 2, 0) / samples.length)));
  const rows = samples.map(s => [1, ...s.reading.features.slice(0, 2).map((v, i) => (v - mean[i]) / scale[i])]);
  const matrix = Array.from({ length: 3 }, (_, i) => Array.from({ length: 3 }, (_, j) => rows.reduce((sum, row) => sum + row[i] * row[j], 0) + (i === j && i > 0 ? 2 : 0)));
  const fit = (axis: 'x' | 'y') => solve(matrix, [0, 1, 2].map(i => rows.reduce((sum, row, j) => sum + row[i] * samples[j].target[axis], 0)));
  return { version: 2, head: { yaw: pose[0].reduce((sum, v) => sum + v, 0) / samples.length, pitch: pose[1].reduce((sum, v) => sum + v, 0) / samples.length }, mean, scale, x: fit('x'), y: fit('y'), center: { x: samples.reduce((sum, s) => sum + s.reading.center.x, 0) / samples.length, y: samples.reduce((sum, s) => sum + s.reading.center.y, 0) / samples.length }, size: samples.reduce((sum, s) => sum + s.reading.size, 0) / samples.length, error: 1, margin: .2, ...viewport, createdAt: Date.now() };
}
export function predictGaze(model: GazeModel, reading: GazeReading): Point {
  const row = [1, ...reading.features.slice(0, 2).map((v, i) => (v - model.mean[i]) / model.scale[i])];
  return { x: row.reduce((sum, v, i) => sum + v * model.x[i], 0), y: row.reduce((sum, v, i) => sum + v * model.y[i], 0) };
}
/** Validation uses new samples, never the points used to train the mapping. */
export function validateGaze(model: GazeModel, samples: GazeSample[]): GazeModel {
  if (GAZE_VALIDATION.some(t => samples.filter(s => s.target.x === t.x && s.target.y === t.y).length < 8)) throw new Error('Accuracy check incomplete. Please retry calibration.');
  if (samples.some(s => Math.abs(s.reading.features[2] - model.head.yaw) > .12 || Math.abs(s.reading.features[3] - model.head.pitch) > .12)) throw new Error('Your head position changed during the accuracy check. Keep it comfortably still and retry calibration.');
  const errors = samples.map(s => { const p = predictGaze(model, s.reading); return Math.hypot(p.x - s.target.x, p.y - s.target.y); });
  const error = errors.reduce((sum, v) => sum + v, 0) / errors.length;
  if (!Number.isFinite(error) || error > .15 || errors.filter(e => e > .3).length / errors.length > .15) throw new Error('Eye tracking is not reliable enough with this camera position. Adjust the camera or lighting, then retry calibration.');
  return { ...model, error, margin: Math.max(.18, Math.min(.3, error * 2)) };
}
export function hasGazeCalibration(model: GazeModel | null): boolean {
  return !!model && model.version === 2 && Number.isFinite(model.error) && model.error >= 0 && model.error <= .15
    && [model.x, model.y].every(axis => axis.length === 3 && axis.every(Number.isFinite))
    && model.mean.length === 2 && model.mean.every(Number.isFinite)
    && model.scale.length === 2 && model.scale.every(value => Number.isFinite(value) && value > 0)
    && Date.now() - model.createdAt >= 0 && Date.now() - model.createdAt < 60 * 60_000;
}

export function gazeIssue(model: GazeModel, reading: GazeReading | null, viewport: { width: number; height: number; camera: string }): string | null {
  if (model.version !== 2 || Date.now() - model.createdAt > 60 * 60_000) return 'Calibration has expired or changed. Recalibrate your viewing area.';
  if (model.camera && viewport.camera !== model.camera) return 'The camera changed. Recalibrate using the current camera.';
  if (!model.width || !model.height || Math.abs(viewport.width / model.width - 1) > .08 || Math.abs(viewport.height / model.height - 1) > .08) return 'The screen size changed. Restore fullscreen and browser zoom, or recalibrate.';
  if (!reading || reading.features.length !== 4 || reading.features.some(v => !Number.isFinite(v))) return 'Your eyes are not clearly tracked. Face the screen with your eyes visible; tracking resumes automatically.';
  if (Math.abs(reading.features[2] - model.head.yaw) > .12 || Math.abs(reading.features[3] - model.head.pitch) > .12) return 'Your head position differs from calibration. Return to your original position, or recalibrate.';
  if (reading.size / model.size < .65 || reading.size / model.size > 1.5 || Math.hypot(reading.center.x - model.center.x, reading.center.y - model.center.y) > model.size * .6) return 'Your distance or position changed. Return to your original position, or recalibrate.';
  return null;
}

export function classifyGaze(model: GazeModel, reading: GazeReading | null, viewport: { width: number; height: number; camera: string }): 'inside' | 'outside' | 'unknown' {
  if (gazeIssue(model, reading, viewport)) return 'unknown';
  const p = predictGaze(model, reading!);
  if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return 'unknown';
  return p.x < -model.margin || p.x > 1 + model.margin || p.y < -model.margin || p.y > 1 + model.margin ? 'outside' : 'inside';
}

/** One warning per sustained episode. Brief blinks freeze the evidence clock;
 * longer uncertainty resets it. Return requires a full second of valid samples. */
export class GazeEpisodes {
  private away = 0;
  private returned = 0;
  private last: number | null = null;
  private lastValid: number | null = null;
  private previous: 'inside' | 'outside' | 'unknown' = 'unknown';
  private warned = false;
  update(value: 'inside' | 'outside' | 'unknown', at: number): 'started' | 'cleared' | null {
    const delta = this.last == null ? 0 : Math.max(0, at - this.last);
    if (delta > GAZE_MAX_GAP_MS || (this.lastValid != null && at - this.lastValid > GAZE_MAX_GAP_MS) || (this.last != null && at < this.last)) { this.away = 0; this.returned = 0; this.previous = 'unknown'; }
    this.last = at;
    if (value === 'unknown') { this.returned = 0; this.previous = value; return null; }
    this.lastValid = at;
    const previous = this.previous; this.previous = value;
    if (value === 'outside') {
      this.returned = 0;
      if (previous === 'inside') this.away = 0;
      else if (previous === 'outside') this.away += delta;
      if (!this.warned && this.away > GAZE_AWAY_MS) { this.warned = true; return 'started'; }
    } else {
      this.away = 0;
      this.returned = previous === 'inside' ? this.returned + delta : 0;
      if (this.warned && this.returned >= GAZE_RETURN_MS) { this.warned = false; return 'cleared'; }
    }
    return null;
  }
  reset(): boolean { const hadWarning = this.warned; this.away = 0; this.returned = 0; this.last = null; this.lastValid = null; this.previous = 'unknown'; this.warned = false; return hadWarning; }
}
