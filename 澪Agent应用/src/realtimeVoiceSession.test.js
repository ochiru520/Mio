import test from 'node:test'
import assert from 'node:assert/strict'
import { createRealtimeVoiceSession } from './realtimeVoiceSession.js'
import { registerMusicPlayer, currentAudioOwner } from './audioCoordinator.js'

const settle = () => new Promise(resolve => setTimeout(resolve, 0))
function fixture(capture) {
  const sockets = [], contexts = [], tracks = [], states = [], errors = [], transcripts = [], replies = []
  const environment = {
    atob, btoa,
    WebSocket: class {
      constructor() { this.readyState = 1; this.sent = []; sockets.push(this) }
      send(value) { this.sent.push(JSON.parse(value)) }
      close() { this.readyState = 3 }
    },
    AudioContext: class {
      constructor() { this.state = 'running'; this.sampleRate = 48000; this.currentTime = 3; this.sources = []; contexts.push(this) }
      close() { this.state = 'closed'; return Promise.resolve() }
      createMediaStreamSource() { return { connect() {}, disconnect() {} } }
      createScriptProcessor() { this.processor = { connect() {}, disconnect() {} }; return this.processor }
      createBuffer(channels, length, rate) { return { duration: length / rate, copyToChannel() {} } }
      createBufferSource() {
        const source = { connect() {}, disconnect() {}, start(time) { this.startTime = time }, stop() { this.stopped = true } }
        this.sources.push(source); return source
      }
    },
    navigator: { mediaDevices: { async getUserMedia() {
      if (capture) return capture()
      const track = { enabled: true, stopped: false, stop() { this.stopped = true } }
      tracks.push(track); return { getTracks: () => [track] }
    } } },
  }
  const session = createRealtimeVoiceSession({ environment, url: () => 'ws://isolated-fixture/voice',
    onState: value => states.push(value), onError: value => errors.push(value),
    onTranscript: (text, final) => transcripts.push({ text, final }), onReply: text => replies.push(text),
  })
  const message = value => sockets.at(-1).onmessage({ data: JSON.stringify(value) })
  return { session, sockets, contexts, tracks, states, errors, transcripts, replies, message }
}

test('real protocol ASR text, mute, ordered PCM and cleanup share one audio owner', async () => {
  const f = fixture(), music = { paused: false, currentTime: 12, pause() { this.paused = true }, play() { this.paused = false; return Promise.resolve() } }
  const cleanup = registerMusicPlayer('realtime-protocol-fixture', music)
  try {
    f.session.connect(); assert.equal(music.paused, true)
    f.sockets[0].onopen(); assert.equal(f.sockets[0].sent[0].type, 'start')
    f.message({ type: 'started' }); await settle()
    assert.equal(f.states.at(-1), 'talking')
    f.message({ type: 'asr', text: '你好澪', final: false }); f.message({ type: 'asr', text: '你好澪。', final: true })
    assert.deepEqual(f.transcripts, [{ text: '你好澪', final: false }, { text: '你好澪。', final: true }])
    assert.equal(f.session.toggleMute(), true); assert.equal(f.tracks[0].enabled, false)
    const audioEvent = { inputBuffer: { getChannelData: () => new Float32Array(4096) } }
    f.contexts[0].processor.onaudioprocess(audioEvent)
    assert.equal(f.sockets[0].sent.length, 1)
    f.session.toggleMute(); f.contexts[0].processor.onaudioprocess(audioEvent)
    assert.equal(f.sockets[0].sent.at(-1).type, 'audio')
    const pcm = btoa(String.fromCharCode(...new Uint8Array(48000)))
    f.message({ type: 'tts_audio', data: pcm }); f.message({ type: 'tts_audio', data: pcm }); await settle()
    assert.deepEqual(f.contexts[0].sources.map(s => s.startTime), [3, 4])
    assert.ok(f.contexts[0].sources.every(s => !s.stopped))
    f.session.stop()
    assert.equal(f.tracks[0].stopped, true); assert.equal(f.contexts[0].state, 'closed')
    assert.ok(f.contexts[0].sources.every(s => s.stopped)); assert.equal(music.paused, false)
    assert.equal(currentAudioOwner(), '')
  } finally { f.session.stop(); cleanup() }
})

test('closing during microphone permission releases late tracks and cannot restart capture', async () => {
  let resolveCapture
  const captured = { stopped: false, stop() { this.stopped = true } }
  const f = fixture(() => new Promise(resolve => { resolveCapture = resolve }))
  try {
    f.session.connect(); f.message({ type: 'started' }); await settle()
    f.session.stop(); resolveCapture({ getTracks: () => [captured] }); await settle()
    assert.equal(captured.stopped, true); assert.equal(f.states.at(-1), 'idle')
    assert.equal(currentAudioOwner(), ''); assert.equal(f.contexts[0].processor, undefined)
  } finally { f.session.stop() }
})

test('old socket callbacks cannot release a new call or overwrite its transcript', async () => {
  const f = fixture()
  try {
    f.session.connect(); const oldMessage = f.sockets[0].onmessage, oldClose = f.sockets[0].onclose
    f.session.connect(); f.message({ type: 'started' }); await settle()
    oldMessage({ data: JSON.stringify({ type: 'error', message: 'old failure' }) }); oldClose()
    oldMessage({ data: JSON.stringify({ type: 'asr', text: 'old transcript', final: true }) })
    assert.equal(f.states.at(-1), 'talking'); assert.notEqual(currentAudioOwner(), '')
    assert.deepEqual(f.transcripts, []); assert.equal(f.tracks[0].stopped, false)
  } finally { f.session.stop() }
})

test('server error cleans microphone and music ownership before retry', async () => {
  const f = fixture()
  try {
    f.session.connect(); f.message({ type: 'started' }); await settle()
    f.message({ type: 'error', message: 'explicit fixture failure' })
    assert.equal(f.states.at(-1), 'error'); assert.equal(f.errors.at(-1), 'explicit fixture failure')
    assert.equal(f.tracks[0].stopped, true); assert.equal(currentAudioOwner(), '')
    f.session.connect(); f.message({ type: 'started' }); await settle()
    assert.equal(f.states.at(-1), 'talking'); assert.equal(f.tracks[1].stopped, false)
  } finally { f.session.stop() }
})

test('microphone rejection does not leave an unhandled promise or audio owner', async () => {
  const f = fixture(() => Promise.reject(new Error('microphone denied fixture')))
  try {
    f.session.connect(); f.message({ type: 'started' }); await settle()
    assert.equal(f.states.at(-1), 'error'); assert.equal(f.errors.at(-1), 'microphone denied fixture')
    assert.equal(f.contexts[0].state, 'closed'); assert.equal(currentAudioOwner(), '')
  } finally { f.session.stop() }
})

test('server stopped releases resources without a later close callback resetting a retry', async () => {
  const f = fixture()
  try {
    f.session.connect(); f.message({ type: 'started' }); await settle()
    f.message({ type: 'stopped' })
    assert.equal(f.states.at(-1), 'idle'); assert.equal(f.tracks[0].stopped, true)
    assert.equal(f.sockets[0].onclose, null); assert.equal(currentAudioOwner(), '')
  } finally { f.session.stop() }
})

test('duplicate started notification does not allocate a second microphone', async () => {
  const f = fixture()
  try {
    f.session.connect(); f.message({ type: 'started' }); f.message({ type: 'started' }); await settle()
    assert.equal(f.tracks.length, 1)
  } finally { f.session.stop() }
})
