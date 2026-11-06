import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';

// Bundled as a classic worker: MediaPipe's WASM loader uses importScripts.
let engine: FaceLandmarker | null = null;
self.onmessage = async (event: MessageEvent<{ kind: string; frame?: ImageBitmap; at?: number }>) => {
  const { kind, frame, at } = event.data;
  try {
    if (kind === 'init') {
      engine = await FaceLandmarker.createFromOptions(await FilesetResolver.forVisionTasks('/preflight/wasm'), {
        baseOptions: { modelAssetPath: '/preflight/face_landmarker.task', delegate: 'CPU' },
        runningMode: 'VIDEO', numFaces: 2, minFaceDetectionConfidence: .65,
        minFacePresenceConfidence: .65, minTrackingConfidence: .65,
      });
      self.postMessage({ kind: 'ready' });
    } else if (kind === 'frame' && frame && engine) {
      const result = engine.detectForVideo(frame, at!);
      self.postMessage({ kind: 'reading', faces: result.faceLandmarks, at, aspect: frame.width / frame.height });
    }
  } catch { self.postMessage({ kind: 'error' }); }
  finally { frame?.close(); }
};
