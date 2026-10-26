import { acquireAudioOwner, releaseAudioOwner } from './audioCoordinator.js'
import { openRealtimeVoiceSocket } from './services/voiceApi.js'

let nextSessionId = 0

/** Own one socket, microphone and ordered PCM queue for the visible call. */
export function createRealtimeVoiceSession(options) {
  const owner = `realtime-voice-${++nextSessionId}`
  const environment = options.environment || globalThis
  let version = 0, socket = null, stream = null, context = null, processor = null, input = null
  let muted = false, microphonePending = false, owned = false, nextAudioTime = 0, timeout = null
  const sources = new Set()
  let playbackQueue = Promise.resolve()
  const active = token => token === version && socket !== null

  function stop() {
    ++version
    clearTimeout(timeout); timeout = null
    const oldSocket = socket; socket = null
    if (oldSocket) {
      oldSocket.onopen = oldSocket.onmessage = oldSocket.onclose = oldSocket.onerror = null
      try { if (oldSocket.readyState === 1) oldSocket.send(JSON.stringify({ type: 'stop' })) } catch (_) {}
      try { oldSocket.close() } catch (_) {}
    }
    if (processor) { processor.onaudioprocess = null; try { processor.disconnect() } catch (_) {} }
    if (input) { try { input.disconnect() } catch (_) {} }
    processor = input = null
    for (const track of stream?.getTracks() || []) track.stop()
    stream = null
    for (const source of sources) {
      source.onended = null
      try { source.stop() } catch (_) {}
      try { source.disconnect() } catch (_) {}
    }
    sources.clear()
    const oldContext = context; context = null
    if (oldContext && oldContext.state !== 'closed') {
      try { void oldContext.close().catch(() => {}) } catch (_) {}
    }
    nextAudioTime = 0; microphonePending = false; muted = false
    playbackQueue = Promise.resolve()
    if (owned) { owned = false; releaseAudioOwner(owner) }
    options.onState('idle')
  }

  function fail(token, error) {
    if (!active(token)) return
    stop()
    options.onError(error?.message || String(error))
    options.onState('error')
  }

  async function audioContext(token) {
    if (!active(token)) return null
    if (!context) {
      const AudioContext = environment.AudioContext || environment.webkitAudioContext
      context = new AudioContext({ sampleRate: 48000 })
    }
    const current = context
    if (current.state === 'suspended') await current.resume()
    return active(token) && current === context ? current : null
  }

  async function startMicrophone(token) {
    if (microphonePending || stream) return
    microphonePending = true
    const audio = await audioContext(token)
    if (!audio) return
    const captured = await environment.navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true,
    } })
    if (!active(token)) { for (const track of captured.getTracks()) track.stop(); return }
    stream = captured
    for (const track of stream.getTracks()) track.enabled = !muted
    input = audio.createMediaStreamSource(stream)
    processor = audio.createScriptProcessor(4096, 1, 1)
    input.connect(processor); processor.connect(audio.destination)
    processor.onaudioprocess = event => {
      if (!active(token) || muted || socket.readyState !== 1) return
      try {
        const samples = event.inputBuffer.getChannelData(0)
        const ratio = audio.sampleRate / 16000
        const pcm = new Uint8Array(Math.floor(samples.length / ratio) * 2)
        const view = new DataView(pcm.buffer)
        for (let i = 0; i < pcm.length / 2; i++) {
          const start = Math.floor(i * ratio), end = Math.min(samples.length, Math.floor((i + 1) * ratio))
          let sum = 0
          for (let j = start; j < end; j++) sum += samples[j]
          const value = Math.max(-1, Math.min(1, end > start ? sum / (end - start) : 0))
          view.setInt16(i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true)
        }
        let binary = ''
        for (let i = 0; i < pcm.length; i += 0x8000) binary += String.fromCharCode(...pcm.subarray(i, i + 0x8000))
        socket.send(JSON.stringify({ type: 'audio', data: environment.btoa(binary) }))
      } catch (error) { fail(token, error) }
    }
    microphonePending = false
    clearTimeout(timeout); timeout = null
    options.onState('talking')
  }

  async function playPcm(token, base64) {
    const audio = await audioContext(token)
    if (!audio) return
    const binary = environment.atob(base64)
    if (!binary.length || binary.length % 2) throw new Error('实时语音音频格式不正确。')
    const bytes = Uint8Array.from(binary, value => value.charCodeAt(0))
    const view = new DataView(bytes.buffer)
    const buffer = audio.createBuffer(1, bytes.length / 2, 24000)
    const samples = new Float32Array(bytes.length / 2)
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 0x8000
    buffer.copyToChannel(samples, 0)
    const source = audio.createBufferSource()
    source.buffer = buffer; source.connect(audio.destination)
    sources.add(source)
    source.onended = () => { sources.delete(source); try { source.disconnect() } catch (_) {} }
    const start = Math.max(audio.currentTime, nextAudioTime)
    nextAudioTime = start + buffer.duration
    source.start(start)
  }

  function connect() {
    stop()
    const token = version
    options.onError(''); options.onState('connecting')
    acquireAudioOwner(owner); owned = true
    try {
      socket = openRealtimeVoiceSocket(environment.location, environment.WebSocket,
        options.url ? { urlOverride: options.url() } : {})
      socket.onopen = () => {
        if (!active(token)) return
        try { socket.send(JSON.stringify({ type: 'start', instructions: options.instructions?.() || '' })) }
        catch (error) { fail(token, error) }
      }
      socket.onmessage = event => {
        if (!active(token)) return
        let message
        try { message = JSON.parse(event.data) } catch (_) { return }
        if (message.type === 'started') void startMicrophone(token).catch(error => fail(token, error))
        else if (message.type === 'asr') options.onTranscript(String(message.text || ''), Boolean(message.final))
        else if (message.type === 'chat') options.onReply(String(message.text || ''))
        else if (message.type === 'tts_audio') playbackQueue = playbackQueue.then(() => playPcm(token, message.data)).catch(error => fail(token, error))
        else if (message.type === 'error') fail(token, message.message || '实时语音服务出现问题。')
        else if (message.type === 'stopped') stop()
      }
      socket.onclose = () => { if (active(token)) stop() }
      socket.onerror = () => fail(token, '实时语音连接失败，请检查云端语音配置。')
      timeout = setTimeout(() => fail(token, '实时语音连接超时，请重试。'), options.timeoutMs || 30000)
    } catch (error) {
      // Construction failed before a socket existed, so normal fail() cannot own it.
      stop(); options.onError(error?.message || String(error)); options.onState('error')
    }
  }

  function toggleMute() {
    muted = !muted
    for (const track of stream?.getTracks() || []) track.enabled = !muted
    return muted
  }
  return { connect, stop, toggleMute }
}
