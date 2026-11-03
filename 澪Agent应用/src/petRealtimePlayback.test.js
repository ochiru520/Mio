import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { babelParse } from 'vue/compiler-sfc'

// Execute the renderer's real function bodies with controlled browser devices.
// Parsing keeps this harness independent of formatting and comments.
const source = readFileSync(new URL('../public/live2d-pet/live2d-pet.js', import.meta.url), 'utf8')
const ast = babelParse(source, { sourceType: 'script' })
const declarations = ast.program.body[0].expression.callee.body.body
function renderer(functions, globals = {}) {
  const selected = functions.map(name => {
    const node = declarations.find(node => node.type === 'FunctionDeclaration' && node.id.name === name)
    assert.ok(node, `Production function exists: ${name}`)
    return source.slice(node.start, node.end)
  }).join('\n')
  const context = vm.createContext({ console, Set, Map, AbortController, ...globals })
  vm.runInContext(selected, context)
  return context
}

test('实时音频请求透传单一response/session，不依赖首句文本重合成', async () => {
  let request
  const c = renderer(['messageResponseId', 'requestSpeechAudio'], {
    settings: {}, fetch: async (url, options) => { request = { url, body: JSON.parse(options.body) }; return { ok: true } },
  })
  await c.requestSpeechAudio('/api/companion/voice/stream', '第一句', { speech_stream: true, response_id: 'call-one', call_session_id: 'session-one' }, new AbortController())
  assert.deepEqual(request.body, { text: '第一句', context: '第一句', emotion: '', model_id: '', language: 'zh', speech_stream: true, response_id: 'call-one', call_session_id: 'session-one' })
})

test('实时音频流失败不会回退并重复播第一句，禁用普通流式设置不禁用实时会话流', async () => {
  const endpoints = [], stopped = []
  const c = renderer(['playSpeech', 'messageResponseId'], {
    settings: { voice_streaming_enabled: false }, shouldSpeak: () => true,
    streamSpeechToken: 0, currentSpeechAbort: null, lastSpeechError: '', lastStreamMode: '', streamFirstAudioLatencyMs: null,
    maxMouthLevel: 0, currentActivity: '', voiceLifecycleActive: false, streamFirstAudioTimer: null,
    performance: { now: () => 100 }, window: { setTimeout: () => 1, clearTimeout() {} },
    applyEmotion() {}, showSpeechBubble() {}, sendSocket() {}, stopSpeech: reason => stopped.push(reason),
    requestSpeechAudio: async endpoint => { endpoints.push(endpoint); throw new Error('synthetic stream failure') },
  })
  await c.playSpeech({ text: '第一句', speech_stream: true, response_id: 'call-one', call_session_id: 'session-one' })
  assert.deepEqual(endpoints, ['/api/companion/voice/stream'])
  assert.deepEqual(stopped, ['error'])
})

test('实时PCM达到80ms开始排播，生命周期在计划播放时刻触发，旧token不能触发首音', () => {
  const timers = [], starts = [], lifecycle = []
  let now = 1000
  const c = renderer(['scheduleAudioBuffer', 'startPendingPcm', 'schedulePcm'], {
    streamSpeechToken: 3, streamGain: {}, streamScheduledUntil: 0, streamSources: new Set(), streamPlaybackActive: false,
    streamHasStarted: false, streamPendingBuffers: [], streamPendingDuration: 0,
    streamFirstAudioTimer: 7, streamAudibleStartTimer: null, streamFirstAudioLatencyMs: null,
    window: { setTimeout: (cb, delay) => { timers.push({ cb, delay }); return timers.length }, clearTimeout() {} },
    performance: { now: () => now }, beginVoiceLifecycle: (...args) => lifecycle.push(args),
    audioBufferFromPcm: bytes => ({ duration: bytes.length / 64000 }),
    audioContext: { currentTime: 10, createBufferSource: () => ({ connect() {}, start(at) { starts.push(at) } }) },
  })
  const message = { speech_stream: true }
  c.schedulePcm(new Uint8Array(1280), {}, message, '回复', 3, 500)
  assert.equal(starts.length, 0)
  c.schedulePcm(new Uint8Array(3840), {}, message, '回复', 3, 500)
  assert.equal(starts.length, 2); assert.equal(lifecycle.length, 0)
  assert.ok(Math.abs(timers[0].delay - 35) < 0.001)
  now = 1035; timers[0].cb()
  assert.equal(lifecycle.length, 1); assert.equal(c.streamFirstAudioLatencyMs, 535)
  c.streamSpeechToken = 4; timers[0].cb()
  assert.equal(lifecycle.length, 1)
})

test('目标插话只移除目标排队音频，拒绝迟到同ID语音且保留其他回复', async () => {
  const stopped = [], played = []
  const c = renderer(['messageResponseId', 'retireSpeechResponse', 'interruptSpeechResponse', 'dispatchSpeech'], {
    retiredSpeechResponses: new Set(), activeResponseId: 'call-current', activeSpeechPriority: 100, activeSpeechSource: 'call',
    speechQueue: [{ response_id: 'call-late' }, { response_id: 'unrelated' }], speechDroppedCount: 0,
    stopSpeech: reason => stopped.push(reason), recordSpeechLifecycle() {},
    shouldSpeak: () => true, trackSpeechRequest: value => value, messagePriority: () => 100,
    speechInProgress: () => false, showSpeechBubble() {}, playSpeech: async message => played.push(message),
  })
  c.interruptSpeechResponse('call-late', 'user_started_speaking')
  assert.equal(c.speechQueue.length, 1); assert.equal(c.speechQueue[0].response_id, 'unrelated'); assert.equal(stopped.length, 0)
  assert.equal(await c.dispatchSpeech({ response_id: 'call-late', text: '过期回复' }), false)
  assert.equal(played.length, 0)
  c.interruptSpeechResponse('call-current', 'user_started_speaking')
  assert.deepEqual(stopped, ['interrupted']); assert.equal(c.speechQueue.length, 1)
})

test('停止陪看只清理观察来源语音，保留正在通话及聊天队列', async () => {
  const stopped = [], played = []
  const c = renderer(['connectSocket', 'messageResponseId', 'retireSpeechResponse', 'interruptSpeechResponse', 'dispatchSpeech'], {
    electronRuntime: true, socketRetryTimer: null, socket: null,
    window: { clearTimeout() {}, location: { protocol: 'http:', host: 'localhost' } },
    WebSocket: function () {},
    retiredSpeechResponses: new Set(), activeResponseId: 'call-current', activeSpeechPriority: 100, activeSpeechSource: 'call',
    speechQueue: [
      { response_id: 'screen-one', source: 'screen' }, { response_id: 'game-one', source: 'game' },
      { response_id: 'wake-one', source: 'desktop_pet_wake' }, { response_id: 'chat-one', source: 'chat' },
      { response_id: 'call-next', source: 'call' },
    ], speechDroppedCount: 0,
    stopSpeech: reason => stopped.push(reason), recordSpeechLifecycle() {},
    shouldSpeak: () => true, trackSpeechRequest: value => value, messagePriority: () => 100,
    speechInProgress: () => false, showSpeechBubble() {}, playSpeech: async message => played.push(message),
  })
  c.connectSocket()
  await c.socket.onmessage({ data: JSON.stringify({ type: 'speech_interrupt', payload: {
    reason: 'observation_stopped', sources: ['screen', 'game', 'desktop_pet_wake'],
  } }) })
  assert.deepEqual(Array.from(c.speechQueue, item => item.response_id), ['chat-one', 'call-next'])
  assert.deepEqual(stopped, [])
  assert.equal(c.speechDroppedCount, 3)
  assert.deepEqual(Array.from(c.retiredSpeechResponses), ['screen-one', 'game-one', 'wake-one'])
  assert.equal(await c.dispatchSpeech({ response_id: 'screen-one', source: 'screen', text: '迟到的观察' }), false)
  assert.equal(played.length, 0)
  assert.equal(c.activeResponseId, 'call-current')
})

test('观察来源停止正在播放的观察，且空或无效来源不会升级成全局打断', () => {
  const stopped = []
  const c = renderer(['messageResponseId', 'retireSpeechResponse', 'interruptSpeechResponse'], {
    retiredSpeechResponses: new Set(), activeResponseId: 'screen-current', activeSpeechSource: 'screen',
    speechQueue: [{ response_id: 'chat-one', source: 'chat' }], speechDroppedCount: 0,
    stopSpeech: reason => stopped.push(reason), recordSpeechLifecycle() {},
  })
  for (const sources of [[], null, 'screen', [false, null, '']]) {
    c.interruptSpeechResponse('', 'observation_stopped', sources)
  }
  assert.deepEqual(stopped, [])
  assert.equal(c.retiredSpeechResponses.size, 0)
  c.interruptSpeechResponse('', 'observation_stopped', ['screen'])
  assert.deepEqual(stopped, ['observation_stopped'])
  assert.deepEqual(Array.from(c.retiredSpeechResponses), ['screen-current'])
  assert.equal(c.speechQueue[0].response_id, 'chat-one')
})

test('开始播放时记录语音来源，下一条聊天不会继承观察来源', async () => {
  const c = renderer(['messageResponseId', 'dispatchSpeech'], {
    retiredSpeechResponses: new Set(), activeResponseId: '', activeSpeechPriority: -1, activeSpeechSource: '',
    shouldSpeak: () => true, trackSpeechRequest: value => value, messagePriority: () => 1,
    speechInProgress: () => false, showSpeechBubble() {}, recordSpeechLifecycle() {}, playSpeech: async () => {},
  })
  await c.dispatchSpeech({ response_id: 'screen-one', text: '观察', source: 'screen' })
  assert.equal(c.activeSpeechSource, 'screen')
  await c.dispatchSpeech({ response_id: 'chat-one', text: '聊天' })
  assert.equal(c.activeSpeechSource, '')
})

test('清理流播放停止所有已排程源并清空尚未开始PCM，防止残音', () => {
  const stopped = [], disconnected = [], cleared = []
  const sources = [1, 2].map(id => ({ onended() {}, stop: () => stopped.push(id), disconnect: () => disconnected.push(id) }))
  const c = renderer(['clearStreamingAudio'], {
    streamSources: new Set(sources), streamPlaybackActive: true, streamScheduledUntil: 50,
    streamPendingBuffers: [{ duration: 2 }], streamPendingDuration: 2, streamHasStarted: true,
    streamAudibleStartTimer: 55, window: { clearTimeout: id => cleared.push(id) },
    streamGain: { disconnect: () => disconnected.push('gain') },
  })
  c.clearStreamingAudio()
  assert.deepEqual(stopped, [1, 2]); assert.deepEqual(disconnected, [1, 2, 'gain']); assert.deepEqual(cleared, [55])
  assert.equal(c.streamSources.size, 0); assert.equal(c.streamPendingBuffers.length, 0)
  assert.equal(c.streamPlaybackActive, false); assert.equal(c.streamScheduledUntil, 0)
  assert.equal(c.streamAudibleStartTimer, null); assert.equal(c.streamGain, null)
})

test('语音播完立即上报结束，气泡仍可保留且不阻塞下一句排播', () => {
  const events = [], timers = []
  let drains = 0
  const bubble = { hidden: false }
  const c = renderer(['stopSpeech'], {
    voiceLifecycleActive: true, activeResponseId: 'screen-one', activeSpeechPriority: 56,
    activeSpeechSource: 'screen', activeSpeechText: '上一句', streamSpeechToken: 1,
    streamFirstAudioTimer: null, streamAudibleStartTimer: null,
    currentSpeechAbort: null, currentAudio: null, currentAudioUrl: '',
    analyser: null, analyserData: null, mouthLevel: 0,
    speechBubble: bubble, speechBubbleHideTimer: null, settings: { bubble_seconds: 30 },
    speechEndCount: 0, speechInterruptCount: 0, speechFinishedCount: 0, speechFailureCount: 0,
    lastSpeechEndedAt: '', lastVoiceEventSent: false,
    finalizePlaybackPerformance: () => ({}), clearStreamingAudio() {},
    hideSpeechBubble: () => { bubble.hidden = true },
    window: { clearTimeout() {}, setTimeout: (cb, delay) => { timers.push({ cb, delay }); return 1 } },
    sendSocket: (type, payload) => { events.push({ type, ...payload }); return true },
    recordSpeechLifecycle() {}, scheduleSpeechDrain: () => { drains += 1 },
  })
  c.stopSpeech('finished')
  assert.equal(bubble.hidden, false)
  assert.equal(events.length, 1)
  assert.equal(events[0].type, 'voice_ended')
  assert.equal(events[0].reason, 'finished')
  assert.equal(events[0].response_id, 'screen-one')
  assert.equal(c.voiceLifecycleActive, false)
  assert.equal(drains, 1)
  assert.equal(timers[0].delay, 30000)
})
