import assert from 'node:assert/strict'
import test from 'node:test'
import { createMessageVoicePlayer, parseMessageWavHeader } from './messageVoicePlayer.js'
import { useChatInteraction } from './composables/useChatInteraction.js'

function wav(pcmBytes = 6400, streaming = true) {
  const bytes = new Uint8Array(44 + pcmBytes), view = new DataView(bytes.buffer)
  for (const [offset, text] of [[0, 'RIFF'], [8, 'WAVE'], [12, 'fmt '], [36, 'data']]) {
    bytes.set(new TextEncoder().encode(text), offset)
  }
  view.setUint32(4, streaming ? 0x7ffffff0 + 36 : 36 + pcmBytes, true)
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, 32000, true); view.setUint32(28, 64000, true)
  view.setUint16(32, 2, true); view.setUint16(34, 16, true)
  view.setUint32(40, streaming ? 0x7ffffff0 : pcmBytes, true)
  for (let offset = 44; offset < bytes.length; offset += 2) view.setInt16(offset, 16384, true)
  return bytes
}
class Context {
  static all = []
  currentTime = 0; destination = {}; sources = []; closed = false
  constructor() { Context.all.push(this) }
  resume() { return Promise.resolve() }
  close() { this.closed = true; return Promise.resolve() }
  createBuffer(channels, frames, rate) {
    const data = Array.from({ length: channels }, () => new Float32Array(frames))
    return { duration: frames / rate, data, getChannelData: channel => data[channel] }
  }
  createBufferSource() {
    const node = { connect() {}, disconnect() {}, stop() { this.stopped = true }, start(when) { this.when = when } }
    this.sources.push(node); return node
  }
}
const pause = () => new Promise(resolve => setTimeout(resolve, 40))
async function waitFor(condition) {
  const deadline = Date.now() + 3000
  while (!condition()) {
    assert.ok(Date.now() < deadline, '播放事件未在限定时间内到达')
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

test('message WAV starts before EOF, decodes fragmented header/frames and waits for actual playback end', async () => {
  const signal = new AbortController(), started = []
  const player = createMessageVoicePlayer({ signal: signal.signal, onStart: () => started.push(true), AudioContextClass: Context })
  let sender
  const response = new Response(new ReadableStream({ start(value) { sender = value } }))
  let complete = false
  const playback = player.consume(response).then(() => { complete = true })
  const bytes = wav()
  sender.enqueue(bytes.slice(0, 7)); sender.enqueue(bytes.slice(7, 43)); sender.enqueue(bytes.slice(43, 6443))
  await waitFor(() => started.length === 1)
  const context = Context.all.at(-1)
  assert.equal(started.length, 1)
  assert.equal(complete, false)
  assert.equal(context.sources[0].buffer.data[0][0], 0.5)
  sender.enqueue(bytes.slice(6443)); sender.close()
  await waitFor(() => context.sources.length === 2)
  assert.equal(complete, false)
  const nodes = context.sources
  assert.ok(nodes[1].when >= nodes[0].when + nodes[0].buffer.duration)
  nodes.forEach(node => node.onended())
  await playback
  assert.equal(complete, true); assert.equal(context.closed, true)
})

test('abort while reading cancels body, scheduled audio and delayed start callback', async () => {
  let sender, cancelled = false, starts = 0
  const control = new AbortController()
  const player = createMessageVoicePlayer({ signal: control.signal, onStart: () => starts++, AudioContextClass: Context })
  const playback = player.consume(new Response(new ReadableStream({
    start(value) { sender = value }, cancel() { cancelled = true },
  })))
  sender.enqueue(wav())
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
  control.abort()
  await assert.rejects(playback, error => error.code === 'request_cancelled')
  await pause()
  assert.equal(starts, 0); assert.equal(cancelled, true)
  assert.equal(Context.all.at(-1).closed, true)
  assert.ok(Context.all.at(-1).sources.every(node => node.stopped))
})

test('invalid/truncated PCM fails clearly and releases playback resources', async () => {
  for (const bytes of [new TextEncoder().encode('not a WAV stream'), wav(0), wav(6400, false).slice(0, 45)]) {
    const player = createMessageVoicePlayer({ AudioContextClass: Context })
    await assert.rejects(player.consume(new Response(bytes)), /WAV|不完整/)
    assert.equal(Context.all.at(-1).closed, true)
  }
  const bad = wav()
  new DataView(bad.buffer).setUint16(32, 7, true)
  assert.throws(() => parseMessageWavHeader(bad), /不受支持/)
})

test('message click requests stream, preserves language/model, and stale stopped playback cannot clear the next turn', async () => {
  const originalFetch = globalThis.fetch, originalContext = globalThis.AudioContext
  const pending = [], bodies = []
  globalThis.AudioContext = Context
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    const request = { url, options, resolve }; pending.push(request)
    options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })
  const deps = { turnId: turn => turn.id, cleanDisplayContent: text => text, chatVoiceLanguage: { value: 'ja' },
    errorMessage: { value: '' }, speakingPartId: { value: '' }, voiceLoadingPartId: { value: '' } }
  const voice = useChatInteraction(deps)
  function respond(index) {
    pending[index].resolve(new Response(new ReadableStream({
      start(sender) { bodies.push(sender); sender.enqueue(wav()) },
      cancel() { pending[index].cancelled = true },
    })))
  }
  try {
    const first = voice.playMessageVoice({ id: 'one', content: '原文', model_id: 'selected-model' })
    assert.equal(pending[0].url, '/api/companion/voice/stream')
    assert.deepEqual(JSON.parse(pending[0].options.body), { text: '原文', language: 'ja', model_id: 'selected-model' })
    respond(0); await pause()
    assert.equal(deps.speakingPartId.value, 'one')
    const oldNode = Context.all.at(-1).sources[0], oldEnded = oldNode.onended
    const second = voice.playMessageVoice({ id: 'two', content: '次の声' })
    await first
    assert.equal(pending[0].cancelled, true)
    assert.equal(pending[1].options.signal.aborted, false)
    assert.equal(deps.voiceLoadingPartId.value, 'two')
    respond(1); await pause()
    oldEnded()
    assert.equal(deps.speakingPartId.value, 'two')
    voice.stopMessageVoice(); await second
    assert.equal(deps.errorMessage.value, '')
    assert.equal(deps.speakingPartId.value, '')
  } finally {
    voice.stopMessageVoice(); globalThis.fetch = originalFetch; globalThis.AudioContext = originalContext
  }
})
