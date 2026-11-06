// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { availableMicrophones, isLoopbackInput, openInterviewMedia, preferredMicrophone } from './microphones';

const device = (deviceId: string, label: string, kind = 'audioinput') => ({deviceId,label,kind,groupId:'',toJSON:()=>({})}) as MediaDeviceInfo;
function track(label: string, id: string) {
  return {label,readyState:'live',getSettings:()=>({deviceId:id}),stop:vi.fn()};
}
function stream(input: ReturnType<typeof track>, camera = false) {
  const tracks = [input];
  const video = {stop:vi.fn()};
  return {getAudioTracks:()=>tracks,getTracks:()=>[...tracks,...(camera?[video]:[])],removeTrack:(item: typeof input)=>tracks.splice(tracks.indexOf(item),1),addTrack:(item: typeof input)=>tracks.push(item),video};
}
function media(list: MediaDeviceInfo[], getUserMedia = vi.fn()) {
  const enumerateDevices = vi.fn().mockResolvedValue(list);
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{enumerateDevices,getUserMedia}});
  return {enumerateDevices,getUserMedia};
}
afterEach(()=>vi.restoreAllMocks());
it('identifies known output-monitor labels without flagging a digital microphone',()=>{
  expect(isLoopbackInput('Monitor of Ryzen HD Audio Controller Analog Stereo')).toBe(true);
  expect(isLoopbackInput('Default - Monitor of USB Audio')).toBe(true);
  expect(isLoopbackInput('alsa_output.pci.monitor')).toBe(true);
  expect(isLoopbackInput('Stereo Mix (Audio)')).toBe(true);
  expect(isLoopbackInput('Ryzen HD Audio Controller Digital Microphone')).toBe(false);
  expect(isLoopbackInput('USB Headset Microphone')).toBe(false);
});
it('chooses a concrete microphone rather than a monitor alias',()=>{
  const inputs=[device('default','Default - Monitor of speakers'),device('loop','Monitor of speakers'),device('mic','Ryzen Digital Microphone')];
  expect(preferredMicrophone(inputs)?.deviceId).toBe('mic');
  expect(preferredMicrophone([device('hidden','')])).toBeUndefined();
});
it('lists actual audio inputs and lets automatic capture use the browser default',async()=>{
  const input=track('Ryzen Digital Microphone','mic');
  const capture=stream(input,true);
  const {getUserMedia}=media([device('loop','Monitor of speakers'),device('mic',input.label),device('speaker','Speakers','audiooutput')],vi.fn().mockResolvedValue(capture));
  expect(await availableMicrophones()).toHaveLength(2);
  expect(await openInterviewMedia('',true)).toBe(capture);
  expect(getUserMedia).toHaveBeenCalledWith({video:{width:{ideal:640},height:{ideal:480}},audio:true});
});
it('honours an explicit headset choice and rejects an explicit loopback before opening it',async()=>{
  const capture=stream(track('USB headset','headset'));
  const {getUserMedia}=media([device('mic','Internal Microphone'),device('headset','USB headset'),device('loop','Monitor of speakers')],vi.fn().mockResolvedValue(capture));
  await openInterviewMedia('headset');
  expect(getUserMedia).toHaveBeenCalledWith({audio:{deviceId:{exact:'headset'}},video:false});
  await expect(openInterviewMedia('loop')).rejects.toThrow('system playback');
  expect(getUserMedia).toHaveBeenCalledTimes(1);
});
it('rechecks labels after permission and replaces a loopback default without restarting video',async()=>{
  const monitor=track('Monitor of speakers','loop'); const original=stream(monitor,true);
  const real=track('Digital Microphone','mic'); const replacement=stream(real);
  const {enumerateDevices,getUserMedia}=media([],vi.fn().mockResolvedValueOnce(original).mockResolvedValueOnce(replacement));
  enumerateDevices.mockResolvedValueOnce([device('','')]).mockResolvedValue([device('loop','Monitor of speakers'),device('mic','Digital Microphone')]);
  const result=await openInterviewMedia('',true);
  expect(result.getAudioTracks()[0]).toBe(real);
  expect(monitor.stop).toHaveBeenCalledOnce();
  expect(original.video.stop).not.toHaveBeenCalled();
  expect(getUserMedia).toHaveBeenCalledTimes(2);
});
it('preserves the camera when permission exposes only a playback monitor',async()=>{
  const monitor=stream(track('Monitor of speakers','loop'),true);
  media([],vi.fn().mockResolvedValue(monitor));
  expect(await openInterviewMedia('',true)).toBe(monitor);
  expect(monitor.getAudioTracks()[0].stop).not.toHaveBeenCalled();
  expect(monitor.video.stop).not.toHaveBeenCalled();
});
it('keeps the camera available when a replacement microphone permission fails',async()=>{
  const monitor=stream(track('Monitor of speakers','loop'),true);
  const {getUserMedia}=media([device('mic','Digital Microphone')],vi.fn().mockResolvedValueOnce(monitor).mockRejectedValueOnce(new DOMException('denied','NotAllowedError')));
  expect(await openInterviewMedia('',true)).toBe(monitor);
  expect(getUserMedia).toHaveBeenCalledTimes(2);
  expect(monitor.video.stop).not.toHaveBeenCalled();
});
it('respects a labelled physical default when looking for a replacement',()=>{
  expect(preferredMicrophone([device('jack','Analog Stereo'),device('default','Ryzen Digital Microphone'),device('digital','Ryzen Digital Microphone')])?.deviceId).toBe('default');
});
it('does not accept mismatched captures and releases their tracks',async()=>{
  const wrong=stream(track('Other Microphone','other'));
  media([device('wanted','USB Microphone')],vi.fn().mockResolvedValue(wrong));
  await expect(openInterviewMedia('wanted')).rejects.toThrow('different microphone');
  expect(wrong.getAudioTracks()[0].stop).toHaveBeenCalled();
});
it('does not silently change a disconnected explicit microphone',async()=>{
  const {getUserMedia}=media([device('other','Another microphone')],vi.fn().mockRejectedValue(new DOMException('unplugged','OverconstrainedError')));
  await expect(openInterviewMedia('unplugged')).rejects.toMatchObject({name:'OverconstrainedError'});
  expect(getUserMedia).toHaveBeenCalledTimes(1);
});
