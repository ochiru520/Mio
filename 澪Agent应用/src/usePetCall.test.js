import test from 'node:test'
import assert from 'node:assert/strict'

import { usePetCall } from './composables/usePetCall.js'
import { acquireAudioOwner, currentAudioOwner } from './audioCoordinator.js'
const ref = value => ({ value })
let now = 1000
globalThis.performance = { now: () => now }
let cleared = []
globalThis.window = { setInterval: () => 71, clearInterval: id => cleared.push(id) }
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve=yes; reject=no }); return {promise, resolve, reject} }
function fixture(state='speaking') {
 const calls=[]
 const d={petCallActive:ref(true), petCallState:ref(state), petCallSettings:ref({pet_call_voice_threshold:0.018}),
  petCallAudioContext:{sampleRate:48000,close:async()=>{}},petCallSessionId:'session-a',petCallNextTurnId:1,
  petCallResponseId:'response-a',petCallAwaitingVoiceSince:0,petCallTurnPending:false,petCallError:ref(''),
  petCallFrames:[],petCallPreRoll:[],petCallSpeechStartedAt:0,petCallSpeechCandidateStartedAt:0,petCallLastVoiceAt:0,
  petCallInterruptSent:false,petCallBargeInStartedAt:0,petCallStream:null,petCallSource:null,petCallProcessor:null,
  petCallMonitorTimer:null, request:async(url,opts)=>{calls.push({url,body:opts?.body?JSON.parse(opts.body):null});return {heard:false}}}
 return {d,calls,api:usePetCall(d)}
}
function frame(api, rms, time) {
 now=time
 const values=new Float32Array(2048).fill(rms)
 api.processPetCallAudio({inputBuffer:{getChannelData:()=>values}})
}
test('生产speaking弱回声大于普通语音门槛仍不打断、不积累正文',()=>{
 const {api,d,calls}=fixture()
 for(let t=1000;t<2200;t+=40)frame(api,0.025,t)
 assert.equal(calls.length,0);assert.equal(d.petCallState.value,'speaking');assert.equal(d.petCallFrames.length,0);assert.equal(d.petCallPreRoll.length,0)
})
test('生产speaking强语音达到360ms仅一次interrupt且会话/response精确',()=>{
 const {api,d,calls}=fixture()
 for(let t=1000;t<1360;t+=40)frame(api,0.09,t)
 assert.equal(calls.length,0)
 frame(api,0.09,1360)
 assert.equal(d.petCallState.value,'listening')
 for(let t=1400;t<1720;t+=40)frame(api,0.09,t)
 assert.deepEqual(calls,[{url:'/api/companion/call/interrupt',body:{call_session_id:'session-a',response_id:'response-a'}}])
})
test('生产短强峰之后弱帧清空360ms候选，不能跨间隙累加打断',()=>{
 const {api,d,calls}=fixture()
 frame(api,0.09,1000);frame(api,0.09,1200);frame(api,0.02,1240);frame(api,0.09,1400);frame(api,0.09,1720)
 assert.equal(calls.length,0);assert.equal(d.petCallBargeInStartedAt,1400)
})
test('生产thinking/waiting_voice/connecting强输入不自听、不提交',()=>{
 for(const state of ['thinking','waiting_voice','connecting']){
  const {api,d,calls}=fixture(state)
  for(let t=1000;t<3000;t+=40)frame(api,0.2,t)
  assert.equal(calls.length,0);assert.equal(d.petCallFrames.length,0);assert.equal(d.petCallPreRoll.length,0)
 }
})
test('生产turn迟到成功/失败不修改新会话状态或pending标记',async()=>{
 for(const failing of [false,true]){
  const {api,d}=fixture('listening'), pending=deferred()
  d.petCallFrames=[new Float32Array(2048).fill(0.1)];d.request=()=>pending.promise
  const old=api.submitPetCallTurn()
  d.petCallSessionId='session-new';d.petCallState.value='listening';d.petCallTurnPending=true;d.petCallNextTurnId=7
  if(failing)pending.reject(new Error('synthetic old failure'));else pending.resolve({heard:true,response_id:'old-response',turn_id:90})
  await old
  assert.equal(d.petCallState.value,'listening');assert.equal(d.petCallResponseId,'response-a');assert.equal(d.petCallNextTurnId,7)
  assert.equal(d.petCallTurnPending,true);assert.equal(d.petCallError.value,'')
 }
})
test('生产结束释放处理器/流轨/AudioContext/定时器/owner且旧callback无效',async()=>{
 const {api,d,calls}=fixture(), stopped=[]
 cleared=[];acquireAudioOwner('pet-call')
 d.petCallMonitorTimer=71;d.petCallProcessor={onaudioprocess:()=>{},disconnect:()=>stopped.push('processor')}
 d.petCallSource={disconnect:()=>stopped.push('source')}
 d.petCallStream={getTracks:()=>[{stop:()=>stopped.push('track')}]}
 d.petCallAudioContext={sampleRate:48000,close:async()=>stopped.push('context')}
 await api.stopPetCall()
 assert.deepEqual(stopped,['processor','source','track','context']);assert.deepEqual(cleared,[71]);assert.equal(currentAudioOwner(),'')
 assert.equal(d.petCallActive.value,false);assert.equal(d.petCallState.value,'idle');assert.equal(d.petCallSessionId,'')
 for(const key of ['petCallProcessor','petCallSource','petCallStream','petCallAudioContext','petCallMonitorTimer'])assert.equal(d[key],null)
 const count=calls.length;frame(api,0.2,4000);assert.equal(calls.length,count)
 assert.equal(calls[0].url,'/api/companion/call/stop')
})
test('连接开始请求迟到后，已结束电话不复活且不再请求麦克风',async()=>{
 const {api,d}=fixture('idle'), pending=deferred();let micCalls=0
 d.petCallActive.value=false;d.petCallSessionId=''
 d.request=url=>url.endsWith('/start')?pending.promise:Promise.resolve({})
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async()=>{micCalls++;return {getAudioTracks:()=>[],getTracks:()=>[]}}}}})
 globalThis.AudioContext=class {sampleRate=48000;destination={};resume=async()=>{};close=async()=>{};createMediaStreamSource(){return {connect(){},disconnect(){}}};createScriptProcessor(){return {connect(){},disconnect(){}}}}
 const start=api.startPetCall()
 await api.togglePetCall()
 pending.resolve({call_session_id:'late-session',next_turn_id:1})
 await start
 try {assert.equal(d.petCallActive.value,false);assert.equal(micCalls,0);assert.equal(d.petCallSessionId,'')}
 finally {await api.stopPetCall({notifyServer:false})}
})
test('麦克风申请迟到仅释放旧流，保留新会话状态',async()=>{
 const {api,d}=fixture('idle'), pending=deferred(), requests=[];let oldTrackStops=0
 d.petCallActive.value=false;d.petCallSessionId=''
 d.request=async(url,options)=>{requests.push({url,body:options?.body?JSON.parse(options.body):null});return {call_session_id:'old-mic-session'}}
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:()=>pending.promise}}})
 const start=api.startPetCall();await Promise.resolve();await Promise.resolve()
 await api.stopPetCall({notifyServer:false})
 d.petCallActive.value=true;d.petCallSessionId='new-session';d.petCallState.value='listening'
 pending.resolve({getTracks:()=>[{stop:()=>oldTrackStops++}]})
 await start
 assert.equal(oldTrackStops,1);assert.equal(d.petCallSessionId,'new-session');assert.equal(d.petCallState.value,'listening')
 assert.equal(d.petCallActive.value,true);assert.equal(requests.at(-1).body.call_session_id,'old-mic-session')
 await api.stopPetCall({notifyServer:false})
})
test('旧AudioContext关闭迟到不会清新会话资源或释放新owner',async()=>{
 const {api,d}=fixture(), closing=deferred()
 d.petCallAudioContext={sampleRate:48000,close:()=>closing.promise}
 const stop=api.stopPetCall({notifyServer:false})
 const newContext={sampleRate:48000,close:async()=>{}}
 d.petCallAudioContext=newContext;d.petCallSessionId='new-session';d.petCallActive.value=true;d.petCallState.value='listening'
 acquireAudioOwner('pet-call')
 closing.resolve();await stop
 assert.equal(d.petCallAudioContext,newContext);assert.equal(d.petCallSessionId,'new-session');assert.equal(d.petCallActive.value,true)
 assert.equal(currentAudioOwner(),'pet-call')
 await api.stopPetCall({notifyServer:false})
})

