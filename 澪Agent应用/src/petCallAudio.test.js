import assert from 'node:assert/strict'
import test from 'node:test'
import { bargeInThreshold, encodePcmWav } from './petCallAudio.js'

test('电话音频编码为16kHz单声道PCM WAV', () => {
  const sourceRate = 48000
  const samples = Float32Array.from({ length: sourceRate }, (_, index) => Math.sin(index / 20) * 0.5)
  const wav = encodePcmWav(samples, sourceRate)
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength)
  assert.equal(new TextDecoder().decode(wav.subarray(0, 4)), 'RIFF')
  assert.equal(new TextDecoder().decode(wav.subarray(8, 12)), 'WAVE')
  assert.equal(view.getUint16(22, true), 1)
  assert.equal(view.getUint32(24, true), 16000)
  assert.equal(view.getUint16(34, true), 16)
  assert.equal(view.getUint32(40, true), 32000)
})

test('播放期间使用更高门槛避免把澪的声音当成用户打断', () => {
  assert.equal(bargeInThreshold(0.018), 0.055)
  assert.equal(bargeInThreshold(0.04), 0.12)
  assert.equal(bargeInThreshold('invalid'), 0.055)
})
