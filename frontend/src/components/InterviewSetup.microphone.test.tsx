// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { InterviewSetup } from './InterviewSetup';
import { availableMicrophones, openInterviewMedia } from '../lib/microphones';

vi.mock('../lib/microphones', async original=>{
  const actual=await original<typeof import('../lib/microphones')>();
  return {...actual,availableMicrophones:vi.fn(),openInterviewMedia:vi.fn()};
});
vi.mock('../lib/audio',()=>({watchMicLevel:vi.fn(()=>vi.fn()),collectRecording:vi.fn(),toWav16k:vi.fn()}));
vi.mock('@mediapipe/tasks-vision',()=>({FaceDetector:{createFromOptions:vi.fn(async()=>({close:vi.fn(),detectForVideo:()=>({detections:[{}]})}))},FilesetResolver:{forVisionTasks:vi.fn(async()=>({}))}}));
const device=(deviceId:string,label:string)=>({deviceId,label,kind:'audioinput',groupId:'',toJSON:()=>({})}) as MediaDeviceInfo;
function mediaStream(id:string,withCamera=false){
  const listeners:Record<string,()=>void>={};
  const mic={label:id==='loop'?'Monitor of speakers':id==='mic'?'Digital Microphone':'USB headset',enabled:true,readyState:'live',getSettings:()=>({deviceId:id}),stop:vi.fn(),addEventListener:vi.fn((kind:string,fn:()=>void)=>{listeners[kind]=fn;})};
  const camera={stop:vi.fn(),addEventListener:vi.fn()};
  return {getTracks:()=>withCamera?[mic,camera]:[mic],getAudioTracks:()=>[mic],getVideoTracks:()=>withCamera?[camera]:[],mic,camera,listeners} as unknown as MediaStream & {mic:typeof mic;camera:typeof camera;listeners:typeof listeners};
}
beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(openInterviewMedia).mockReset();
  vi.mocked(availableMicrophones).mockReset();
  Object.defineProperty(window,'isSecureContext',{configurable:true,value:true});
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),getDisplayMedia:vi.fn()}});
  vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue();
  vi.mocked(availableMicrophones).mockResolvedValue([device('loop','Monitor of speakers'),device('mic','Digital Microphone'),device('headset','USB headset')]);
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function open(){return render(<InterviewSetup token="fixture" onReady={()=>{}} onContinue={()=>{}}/>);}
it('lists microphones before asking for permission and marks monitor inputs unusable',async()=>{
  open();
  const loop=await screen.findByRole('option',{name:/Monitor of speakers.*system audio/});
  expect((loop as HTMLOptionElement).disabled).toBe(true);
  expect(screen.getByRole('option',{name:'Digital Microphone'})).toBeTruthy();
  expect(openInterviewMedia).not.toHaveBeenCalled();
});
it('uses the selected input and switches microphone without stopping camera or screen sharing',async()=>{
  const initial=mediaStream('mic',true),headset=mediaStream('headset');
  vi.mocked(openInterviewMedia).mockResolvedValueOnce(initial).mockResolvedValueOnce(headset);
  const displayTrack={readyState:'live',stop:vi.fn(),getSettings:()=>({displaySurface:'monitor'}),addEventListener:vi.fn()};
  vi.mocked(navigator.mediaDevices.getDisplayMedia).mockResolvedValue({getTracks:()=>[displayTrack],getVideoTracks:()=>[displayTrack]} as unknown as MediaStream);
  open();await screen.findByRole('option',{name:'Digital Microphone'});
  fireEvent.change(screen.getByLabelText('Microphone'),{target:{value:'mic'}});
  fireEvent.click(screen.getByRole('button',{name:'Enable camera & microphone'}));
  await screen.findByText('Connected: Digital Microphone');
  await waitFor(()=>expect((screen.getByRole('button',{name:'Share entire screen'}) as HTMLButtonElement).disabled).toBe(false));
  expect(openInterviewMedia).toHaveBeenCalledWith('mic',true);
  fireEvent.click(screen.getByRole('button',{name:'Share entire screen'}));
  await screen.findByText('Entire screen shared');
  fireEvent.change(screen.getByLabelText('Microphone'),{target:{value:'headset'}});
  await screen.findByText('Connected: USB headset');
  expect(openInterviewMedia).toHaveBeenLastCalledWith('headset');
  expect(initial.mic.stop).toHaveBeenCalled();
  expect(initial.camera.stop).not.toHaveBeenCalled();
  expect(displayTrack.stop).not.toHaveBeenCalled();
  expect(screen.getByText('Entire screen shared')).toBeTruthy();
  expect((screen.getByRole('button',{name:'Record a 4-second sample'}) as HTMLButtonElement).disabled).toBe(false);
});
it('reports a disconnected mic and disables recording instead of showing a usable input',async()=>{
  const initial=mediaStream('mic',true);vi.mocked(openInterviewMedia).mockResolvedValue(initial);
  open();fireEvent.click(screen.getByRole('button',{name:'Enable camera & microphone'}));
  await screen.findByText('Connected: Digital Microphone');
  await act(async()=>initial.listeners.ended());
  await screen.findByText(/microphone disconnected/);
  expect((screen.getByRole('button',{name:'Record a 4-second sample'}) as HTMLButtonElement).disabled).toBe(true);
});
it('keeps camera setup recoverable with a browser-limited monitor and reconnects automatically to the restored mic',async()=>{
  const monitor=mediaStream('loop',true),real=mediaStream('mic',true);
  vi.mocked(openInterviewMedia).mockResolvedValueOnce(monitor).mockResolvedValueOnce(real);
  open();fireEvent.click(screen.getByRole('button',{name:'Enable camera & microphone'}));
  await screen.findByText(/browser selected speaker playback/);
  await waitFor(()=>expect((screen.getByRole('button',{name:'Reconnect devices'}) as HTMLButtonElement).disabled).toBe(false));
  expect(monitor.camera.stop).not.toHaveBeenCalled();
  expect((screen.getByRole('button',{name:'Record a 4-second sample'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Reconnect devices'}));
  await screen.findByText('Connected: Digital Microphone');
  expect(openInterviewMedia).toHaveBeenLastCalledWith('',true);
  await waitFor(()=>expect((screen.getByRole('button',{name:'Record a 4-second sample'}) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByText(/browser selected speaker playback/)).toBeNull();
});
it('refreshes after an initial permission failure so the microphone selection is recoverable',async()=>{
  vi.mocked(openInterviewMedia).mockRejectedValue(new DOMException('denied','NotAllowedError'));
  open();fireEvent.click(screen.getByRole('button',{name:'Enable camera & microphone'}));
  await screen.findByText(/permission was denied/);
  expect(screen.getByRole('option',{name:'Digital Microphone'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Refresh microphones'}));
  await waitFor(()=>expect(availableMicrophones).toHaveBeenCalledTimes(3));
});
