/** Browser device labels are hints, not proof of hardware identity. */
export function isLoopbackInput(label: string): boolean {
  return /(?:^|\b)(?:monitor of|monitor_of|loopback|stereo mix|what u hear)(?:\b|_)/i.test(label)
    || /(?:^|\.)monitor(?:$|\s)/i.test(label);
}

export function microphoneLabel(device: Pick<MediaDeviceInfo, 'label'>, index: number): string {
  return `${device.label || `Microphone ${index + 1}`}${isLoopbackInput(device.label) ? ' (system audio, not a microphone)' : ''}`;
}

export async function availableMicrophones(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  return (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput');
}

export function preferredMicrophone(devices: MediaDeviceInfo[]): MediaDeviceInfo | undefined {
  // Respect a usable system default before trying another labelled input.
  return devices.find(device => device.deviceId === 'default' && device.label && !isLoopbackInput(device.label))
    ?? devices.find(device => device.deviceId && !['default','communications'].includes(device.deviceId) && device.label && !isLoopbackInput(device.label))
    ?? devices.find(device => device.deviceId && device.label && !isLoopbackInput(device.label));
}

export function microphoneError(error: unknown): string {
  if (error instanceof Error || error instanceof DOMException) {
    if (error.name === 'NotAllowedError') return 'Camera or microphone permission was denied. Allow access in the browser and select your real microphone in its permission picker.';
    if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') return 'The selected microphone is unavailable. Refresh the device list and choose a connected microphone.';
    if (error.name === 'NotReadableError' || error.name === 'AbortError') return 'The selected camera or microphone could not start. Check that it is connected and available, then retry.';
    return error.message;
  }
  return 'The microphone could not start. Check your input device and browser permissions.';
}

/** Let the browser use its recording default unless the candidate chose an input.
 * Enumeration can hide microphones until permission is granted. A monitor-only list
 * does not prove that hardware is absent; keep video available so setup can recover.
 * Setup separately prevents a known playback monitor from passing the mic check. */
export async function openInterviewMedia(deviceId = '', withVideo = false): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Open this page over HTTPS or localhost in a supported browser.');
  const devices = await availableMicrophones().catch(() => []);
  const chosen = deviceId ? devices.find(device => device.deviceId === deviceId) : undefined;
  if (chosen && isLoopbackInput(chosen.label)) throw new Error('This input captures system playback. Choose your built-in, USB or headset microphone instead.');
  const stream = await navigator.mediaDevices.getUserMedia({
    video: withVideo ? { width: { ideal: 640 }, height: { ideal: 480 } } : false,
    audio: deviceId ? { deviceId: { exact: deviceId } } : true,
  });
  try {
    const track = stream.getAudioTracks()[0];
    if (!track || track.readyState !== 'live') throw new Error('No live microphone was returned. Choose a connected input device.');
    if (deviceId && !['default','communications'].includes(deviceId) && track.getSettings().deviceId && track.getSettings().deviceId !== deviceId) {
      throw new Error('The browser opened a different microphone. Choose the requested input in the browser permission picker and retry.');
    }
    if (isLoopbackInput(track.label)) {
      if (deviceId) throw new Error('The selected source is system audio. Choose your built-in, USB or headset microphone.');
      const refreshed = await availableMicrophones().catch(() => []);
      const replacement = preferredMicrophone(refreshed.filter(device => device.deviceId !== track.getSettings().deviceId));
      if (replacement) {
        // A hidden or stale permission may reject this input. Preserve the initial
        // camera stream and let setup offer selection/reconnection in that case.
        let audio: MediaStream | undefined;
        try { audio = await openInterviewMedia(replacement.deviceId); } catch { /* Recover in setup. */ }
        if (audio) {
          stream.removeTrack(track); track.stop();
          audio.getAudioTracks().forEach(input => stream.addTrack(input));
        }
      }
    }
    return stream;
  } catch (error) {
    stream.getTracks().forEach(track => track.stop());
    throw error;
  }
}
