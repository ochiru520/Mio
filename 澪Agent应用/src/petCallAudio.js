export function encodePcmWav(samples, sourceRate, targetRate = 16000) {
  const ratio = sourceRate / targetRate
  const outputLength = Math.max(1, Math.floor(samples.length / ratio))
  const output = new Float32Array(outputLength)
  for (let index = 0; index < outputLength; index += 1) {
    const start = Math.floor(index * ratio)
    const end = Math.min(samples.length, Math.max(start + 1, Math.floor((index + 1) * ratio)))
    let total = 0
    for (let cursor = start; cursor < end; cursor += 1) total += samples[cursor]
    output[index] = total / (end - start)
  }
  const buffer = new ArrayBuffer(44 + output.length * 2)
  const view = new DataView(buffer)
  const write = (offset, value) => [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)))
  write(0, 'RIFF')
  view.setUint32(4, 36 + output.length * 2, true)
  write(8, 'WAVE')
  write(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, targetRate, true)
  view.setUint32(28, targetRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  write(36, 'data')
  view.setUint32(40, output.length * 2, true)
  output.forEach((sample, index) => {
    const clamped = Math.max(-1, Math.min(1, sample))
    view.setInt16(44 + index * 2, clamped < 0 ? clamped * 32768 : clamped * 32767, true)
  })
  return new Uint8Array(buffer)
}

export function bytesToBase64(bytes) {
  let binary = ''
  const block = 0x8000
  for (let offset = 0; offset < bytes.length; offset += block) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + block))
  }
  return btoa(binary)
}

export function bargeInThreshold(baseThreshold) {
  const normalized = Number.isFinite(Number(baseThreshold)) ? Number(baseThreshold) : 0.018
  return Math.max(0.055, normalized * 3)
}
