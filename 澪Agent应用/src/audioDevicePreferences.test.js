import test from 'node:test'
import assert from 'node:assert/strict'
import { microphoneConstraints, microphoneError, readAudioInputPreferences } from './audioDevicePreferences.js'

test('已保存的输入设备与外放方式传给实际麦克风请求', () => {
  const storage = { getItem: key => ({ 'mio.audio.input': 'mic-2', 'mio.audio.mode': 'speakers' })[key] }
  const constraints = microphoneConstraints(readAudioInputPreferences(storage))
  assert.deepEqual(constraints.deviceId, { exact: 'mic-2' })
  assert.equal(constraints.echoCancellation, true)
  assert.equal(constraints.noiseSuppression, true)
})

test('没有保存或存储不可用时使用默认设备和耳机模式', () => {
  const constraints = microphoneConstraints(readAudioInputPreferences({ getItem() { throw Error('denied') } }))
  assert.equal(constraints.deviceId, undefined)
  assert.equal(constraints.echoCancellation, false)
})

test('设备权限、断开与占用都有可操作提示', () => {
  assert.match(microphoneError({ name: 'NotAllowedError' }), /允许访问/)
  assert.match(microphoneError({ name: 'OverconstrainedError' }), /重新选择/)
  assert.match(microphoneError({ name: 'NotReadableError' }), /占用/)
})
