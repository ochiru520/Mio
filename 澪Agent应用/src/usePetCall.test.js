import test from 'node:test'
import assert from 'node:assert/strict'

import { usePetCall } from './composables/usePetCall.js'
import { acquireAudioOwner, currentAudioOwner } from './audioCoordinator.js'
const ref = value => ({ value })
let now = 1000
globalThis.performance = { now: () => now }
let cleared = []
let monitor = null
globalThis.window = { setInterval: callback => { monitor=callback; return 71 }, clearInterval: id => cleared.push(id) }
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
test('连接中不采集；thinking/waiting_voice弱回声不打断，强语音360ms可打断',async()=>{
 for(const state of ['thinking','waiting_voice','connecting']){
  const {api,d,calls}=fixture(state)
  for(let t=1000;t<2000;t+=40)frame(api,0.025,t)
  assert.equal(calls.length,0);assert.equal(d.petCallFrames.length,0)
  for(let t=2100;t<=2500;t+=40)frame(api,0.2,t)
  assert.equal(calls.filter(x=>x.url.endsWith('/interrupt')).length,state==='connecting'?0:1)
  assert.equal(d.petCallState.value,state==='connecting'?'connecting':'listening')
  await Promise.resolve();await Promise.resolve()
  await api.stopPetCall({notifyServer:false})
 }
})

test('同会话旧turn被插话后迟到成功不覆盖新录音或下一轮编号',async()=>{
 const {api,d}=fixture('listening'), oldReply=deferred(), interrupt=deferred(), urls=[]
 d.request=(url,opts)=>{
  urls.push({url,opts})
  if(url.endsWith('/turn'))return oldReply.promise
  if(url.endsWith('/interrupt'))return interrupt.promise
  return Promise.resolve({call_session_id:'session-a',next_turn_id:2})
 }
 d.petCallFrames=[new Float32Array(2048).fill(0.1)]
 const old=api.submitPetCallTurn()
 for(let t=2000;t<=2400;t+=40)frame(api,0.2,t)
 assert.equal(urls[0].opts.signal.aborted,true)
 assert.equal(d.petCallState.value,'listening')
 const recorded=d.petCallFrames.length
 oldReply.resolve({heard:true,response_id:'old-response',turn_id:1});await old
 assert.equal(d.petCallState.value,'listening');assert.equal(d.petCallFrames.length,recorded)
 assert.equal(d.petCallResponseId,'');assert.equal(d.petCallTurnPending,false)
 interrupt.resolve({});await Promise.resolve();await Promise.resolve();await Promise.resolve()
 await api.stopPetCall({notifyServer:false})
})

test('插话新录音等取消确认及轮次同步后提交，旧finally不清新pending',async()=>{
 const {api,d}=fixture('listening'), oldReply=deferred(), interrupt=deferred(), nextReply=deferred(), turns=[]
 d.request=(url,opts)=>{
  if(url.endsWith('/turn')){turns.push(JSON.parse(opts.body));return turns.length===1?oldReply.promise:nextReply.promise}
  if(url.endsWith('/interrupt'))return interrupt.promise
  return Promise.resolve({call_session_id:'session-a',next_turn_id:2})
 }
 d.petCallFrames=[new Float32Array(2048).fill(0.1)];const old=api.submitPetCallTurn()
 for(let t=2000;t<=2400;t+=40)frame(api,0.2,t)
 const next=api.submitPetCallTurn();assert.equal(turns.length,1)
 interrupt.resolve({});await new Promise(resolve=>setImmediate(resolve))
 assert.equal(turns.length,2);assert.equal(turns[1].turn_id,2)
 oldReply.resolve({heard:true,response_id:'old-response',turn_id:1});await old
 assert.equal(d.petCallTurnPending,true)
 nextReply.resolve({heard:true,response_id:'new-response',turn_id:2});await next
 assert.equal(d.petCallResponseId,'new-response');assert.equal(d.petCallState.value,'waiting_voice')
 await api.stopPetCall({notifyServer:false})
})

test('interrupt先到服务端时携带当前提交turn_id，跳轮确认后旧POST迟到409不污染新轮',async()=>{
 const {api,d}=fixture('listening'), delayedPost=deferred(), secondPost=deferred(), turns=[], interrupts=[]
 d.request=(url,opts)=>{
  if(url.endsWith('/turn')){turns.push(JSON.parse(opts.body));return turns.length===1?delayedPost.promise:secondPost.promise}
  if(url.endsWith('/interrupt')){interrupts.push(JSON.parse(opts.body));return Promise.resolve({next_turn_id:2})}
  throw new Error('新后端直接返回下一轮，无需额外status请求')
 }
 d.petCallFrames=[new Float32Array(2048).fill(0.1)];const old=api.submitPetCallTurn()
 for(let t=2000;t<=2400;t+=40)frame(api,0.2,t)
 assert.deepEqual(interrupts,[{call_session_id:'session-a',response_id:'',turn_id:1}])
 const next=api.submitPetCallTurn();await new Promise(resolve=>setImmediate(resolve))
 assert.equal(turns[1].turn_id,2)
 delayedPost.reject(new Error('HTTP409 旧轮次已跳过'));await old
 assert.equal(d.petCallError.value,'');assert.equal(d.petCallTurnPending,true)
 secondPost.resolve({heard:true,response_id:'call-new',turn_id:2});await next
 assert.equal(d.petCallResponseId,'call-new');assert.equal(d.petCallState.value,'waiting_voice')
 await api.stopPetCall({notifyServer:false})
})

async function startMonitoredFixture() {
 const f=fixture('idle');f.d.petCallActive.value=false;f.d.petCallSessionId=''
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async()=>({getAudioTracks:()=>[],getTracks:()=>[]})}}})
 globalThis.AudioContext=class {sampleRate=48000;destination={};resume=async()=>{};close=async()=>{};createMediaStreamSource(){return {connect(){},disconnect(){}}};createScriptProcessor(){return {connect(){},disconnect(){}}}}
 f.d.request=async()=>({call_session_id:'session-a',next_turn_id:1})
 await f.api.startPetCall()
 return {...f,poll:monitor}
}

test('正式通话使用已选输入，麦克风断开清理并允许重新开始', async () => {
 const f=fixture('idle');f.d.petCallActive.value=false;f.d.petCallSessionId=''
 const oldStorage=Object.getOwnPropertyDescriptor(globalThis,'localStorage')
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>({'mio.audio.input':'physical-mic','mio.audio.mode':'speakers'})[key]}})
 const listeners=new Map(), oldAdd=window.addEventListener, oldRemove=window.removeEventListener
 window.addEventListener=(name,handler)=>listeners.set(name,handler)
 window.removeEventListener=(name)=>listeners.delete(name)
 let requested,stops=0
 const track={stop:()=>stops++,getSettings:()=>({deviceId:'physical-mic'})}
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async constraints=>{requested=constraints;return {getAudioTracks:()=>[track],getTracks:()=>[track]}}}}})
 globalThis.AudioContext=class {sampleRate=48000;destination={};resume=async()=>{};close=async()=>{};createMediaStreamSource(){return {connect(){},disconnect(){}}};createScriptProcessor(){return {connect(){},disconnect(){}}}}
 f.d.request=async()=>({call_session_id:'session-device',next_turn_id:1})
 try {
  await f.api.startPetCall()
  assert.deepEqual(requested.audio.deviceId,{exact:'physical-mic'});assert.equal(requested.audio.echoCancellation,true)
  assert.equal(f.d.petCallActive.value,true)
  const ended=track.onended;ended();await Promise.resolve();await Promise.resolve()
  assert.equal(f.d.petCallActive.value,false);assert.match(f.d.petCallError.value,/已断开/)
  assert.equal(stops,1);assert.equal(listeners.size,0);assert.equal(track.onended,null)
  await f.api.startPetCall();assert.equal(f.d.petCallActive.value,true)
  listeners.get('mio-audio-devices-changed')();await Promise.resolve();await Promise.resolve()
  assert.equal(f.d.petCallActive.value,false);assert.match(f.d.petCallError.value,/重新开始通话/)
 } finally {
  await f.api.stopPetCall({notifyServer:false})
  window.addEventListener=oldAdd;window.removeEventListener=oldRemove
  if(oldStorage)Object.defineProperty(globalThis,'localStorage',oldStorage);else delete globalThis.localStorage
 }
})

test('首音早于turn POST返回时不被覆盖为waiting_voice，已结束不复活',async()=>{
 for(const ended of [false,true]){
  const {api,d,poll}=await startMonitoredFixture(), reply=deferred()
  d.request=url=>url.endsWith('/turn')?reply.promise:Promise.resolve({active:true,call_session_id:'session-a',current_turn_id:1,current_response_id:'call-stream-1',next_turn_id:2,voice_started:{response_id:'call-stream-1'},voice_ended:ended?{response_id:'call-stream-1',reason:'finished'}:{}})
  d.petCallFrames=[new Float32Array(2048).fill(0.1)];const pending=api.submitPetCallTurn()
  await poll();assert.equal(d.petCallState.value,ended?'listening':'speaking')
  reply.resolve({heard:true,response_id:'call-stream-1',turn_id:1});await pending
  assert.equal(d.petCallState.value,ended?'listening':'speaking')
  assert.equal(d.petCallResponseId,ended?'':'call-stream-1')
  await api.stopPetCall({notifyServer:false})
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
  assert.equal(d.petCallState.value,'listening');assert.equal(d.petCallResponseId,'');assert.equal(d.petCallNextTurnId,7)
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
