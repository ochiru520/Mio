import assert from 'node:assert/strict'
import test from 'node:test'
import { useChatInteraction } from './composables/useChatInteraction.js'

test('switching voice turns keeps the new request alive after old cancellation settles', async () => {
  const originalFetch = globalThis.fetch, originalAudio = globalThis.Audio
  const pending = [], played = []
  globalThis.fetch = (_url, options) => new Promise((resolve, reject) => {
    const request = { resolve, signal: options.signal }
    pending.push(request)
    options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })
  globalThis.Audio = class {
    constructor(src) { this.src = src; this.handlers = {} }
    addEventListener(type, callback) { this.handlers[type] = callback }
    pause() {}
    async play() { played.push(this) }
  }
  const deps = { turnId: turn => turn.id, cleanDisplayContent: value => value,
    chatVoiceLanguage: {value:'zh'}, errorMessage: {value:''}, speakingPartId: {value:''},
    voiceLoadingPartId: {value:''}, activeMessageAudio: null, activeMessageAudioUrl: '', voiceAbortController: null }
  const voice = useChatInteraction(deps)
  try {
    const first = voice.playMessageVoice({id:'first',content:'第一句'})
    assert.equal(pending.length, 1)
    const second = voice.playMessageVoice({id:'second',content:'第二句'})
    await first
    assert.equal(pending[0].signal.aborted, true)
    assert.equal(pending[1].signal.aborted, false)
    assert.equal(deps.voiceLoadingPartId.value, 'second')
    pending[1].resolve(new Response(new Blob(['synthetic-unit-audio']), {status:200}))
    await second
    assert.equal(deps.speakingPartId.value, 'second')
    assert.equal(played.length, 1)
    assert.equal(deps.errorMessage.value, '')
    const oldAudio = played[0]
    const third = voice.playMessageVoice({id:'third',content:'第三句'})
    pending[2].resolve(new Response(new Blob(['synthetic-unit-audio']), {status:200}))
    await third
    oldAudio.handlers.error()
    oldAudio.handlers.ended()
    assert.equal(deps.speakingPartId.value, 'third')
    assert.equal(played.length, 2)
  } finally {
    voice.stopMessageVoice()
    globalThis.fetch = originalFetch; globalThis.Audio = originalAudio
  }
})
