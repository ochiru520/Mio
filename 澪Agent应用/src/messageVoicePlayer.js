/** Incremental WAV playback for message reading; the HTTP body stays streaming. */
const MAX_AUDIO_BYTES = 64 * 1024 * 1024
const ascii = (bytes, offset) => String.fromCharCode(...bytes.subarray(offset, offset + 4))
const cancelled = () => Object.assign(new Error('请求已取消'), { code: 'request_cancelled' })
function append(a, b) {
  const result = new Uint8Array(a.length + b.length)
  result.set(a); result.set(b, a.length)
  return result
}

export function parseMessageWavHeader(bytes) {
  if (bytes.length < 12) return null
  if (ascii(bytes, 0) !== 'RIFF' || ascii(bytes, 8) !== 'WAVE') throw new Error('语音没有返回有效 WAV')
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  let format, offset = 12
  while (offset + 8 <= bytes.length) {
    const id = ascii(bytes, offset), size = view.getUint32(offset + 4, true), start = offset + 8
    if (id === 'fmt ') {
      if (size < 16) throw new Error('语音 WAV 格式不完整')
      if (bytes.length < start + 16) return null
      format = { tag: view.getUint16(start, true), channels: view.getUint16(start + 2, true),
        rate: view.getUint32(start + 4, true), align: view.getUint16(start + 12, true),
        bits: view.getUint16(start + 14, true) }
      if (![1, 2].includes(format.channels) || format.rate < 8000 || format.rate > 192000
        || !(format.tag === 1 && [8, 16, 24, 32].includes(format.bits) || format.tag === 3 && format.bits === 32)
        || format.align !== format.channels * format.bits / 8) throw new Error('语音 WAV 格式不受支持')
    } else if (id === 'data') {
      if (!format) throw new Error('语音 WAV 缺少格式信息')
      // Large RIFF/data sizes are the backend's streaming-length sentinel.
      return { ...format, offset: start, bytes: size <= MAX_AUDIO_BYTES ? size : null }
    }
    offset = start + size + size % 2
    if (offset > 65536) throw new Error('语音 WAV 头过大')
  }
  if (bytes.length > 65536) throw new Error('语音 WAV 缺少音频数据')
  return null
}

function sample(view, offset, format) {
  if (format.tag === 3) {
    const value = view.getFloat32(offset, true)
    if (!Number.isFinite(value)) throw new Error('语音包含无效音频采样')
    return Math.max(-1, Math.min(1, value))
  }
  if (format.bits === 8) return (view.getUint8(offset) - 128) / 128
  if (format.bits === 16) return view.getInt16(offset, true) / 32768
  if (format.bits === 32) return view.getInt32(offset, true) / 2147483648
  const value = view.getUint8(offset) | view.getUint8(offset + 1) << 8 | view.getInt8(offset + 2) << 16
  return value / 8388608
}

export function createMessageVoicePlayer({ signal, onStart, AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext }) {
  // Construct and resume inside the click gesture, before any HTTP await.
  const context = new AudioContextClass({ latencyHint: 'interactive' })
  const ready = Promise.resolve(context.resume())
  void ready.catch(() => {})
  const sources = new Set()
  let stopped = false, reader, startTimer, scheduled = 0, started = false, eof = false
  let pending = [], pendingDuration = 0, resolveDrained
  const drained = new Promise(resolve => { resolveDrained = resolve })
  function check() { if (stopped || signal?.aborted) throw cancelled() }
  function stop() {
    if (stopped) return
    stopped = true
    clearTimeout(startTimer)
    signal?.removeEventListener('abort', stop)
    for (const source of sources) {
      source.onended = null
      try { source.stop() } catch (_) {}
      source.disconnect()
    }
    sources.clear(); pending = []; pendingDuration = 0
    if (reader) void reader.cancel().catch(() => {})
    void context.close().catch(() => {})
    resolveDrained()
  }
  signal?.addEventListener('abort', stop, { once: true })
  if (signal?.aborted) stop()
  function schedule(buffer) {
    check()
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    const when = Math.max(context.currentTime + 0.025, scheduled)
    scheduled = when + buffer.duration
    sources.add(source)
    source.onended = () => {
      sources.delete(source); source.disconnect()
      if (eof && !sources.size) resolveDrained()
    }
    source.start(when)
    if (!started) {
      started = true
      startTimer = setTimeout(() => { if (!stopped && !signal?.aborted) onStart?.() }, Math.max(0, (when - context.currentTime) * 1000))
    }
  }
  function flush() {
    for (const buffer of pending) schedule(buffer)
    pending = []; pendingDuration = 0
  }
  function queue(bytes, format) {
    const frames = bytes.length / format.align
    if (!frames) return
    const buffer = context.createBuffer(format.channels, frames, format.rate)
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    for (let channel = 0; channel < format.channels; channel++) {
      const output = buffer.getChannelData(channel)
      for (let frame = 0; frame < frames; frame++) output[frame] = sample(view, frame * format.align + channel * format.bits / 8, format)
    }
    if (started) schedule(buffer)
    else {
      pending.push(buffer); pendingDuration += buffer.duration
      if (pendingDuration >= 0.08) flush()
    }
  }
  async function consume(response) {
    let pendingBytes = new Uint8Array(), format, received = 0, dataBytes = 0
    try {
      check(); await ready; check()
      if (!response.body?.getReader) throw new Error('浏览器无法读取流式音频')
      reader = response.body.getReader()
      while (true) {
        const { done, value } = await reader.read()
        check()
        if (value?.length) {
          received += value.length
          if (received > MAX_AUDIO_BYTES) throw new Error('语音超过播放大小限制')
          pendingBytes = append(pendingBytes, value)
        }
        if (!format) {
          format = parseMessageWavHeader(pendingBytes)
          if (format) pendingBytes = pendingBytes.slice(format.offset)
        }
        if (format) {
          const remaining = format.bytes === null ? pendingBytes.length : Math.min(pendingBytes.length, format.bytes - dataBytes)
          const aligned = remaining - remaining % format.align
          if (aligned && (done || aligned >= Math.floor(format.rate * 0.04) * format.align)) {
            queue(pendingBytes.subarray(0, aligned), format)
            dataBytes += aligned; pendingBytes = pendingBytes.slice(aligned)
          }
        }
        if (done) break
      }
      if (!format || !dataBytes || (format.bytes !== null && dataBytes !== format.bytes)
        || (format.bytes === null && pendingBytes.length)) throw new Error('语音流不完整，请重试')
      flush(); eof = true
      if (!sources.size) resolveDrained()
      await drained; check()
    } finally {
      if (reader) { try { await reader.cancel() } catch (_) {} }
      try { reader?.releaseLock() } catch (_) {}
      reader = null
      stop()
    }
  }
  return { consume, stop }
}
