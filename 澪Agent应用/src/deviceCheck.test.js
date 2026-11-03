import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { nextTick, ref } from 'vue'

function setup(mediaDevices) {
  const file = fs.readFileSync(new URL('./components/DeviceCheckPanel.vue', import.meta.url), 'utf8')
  const script = file.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const events = [], storage = new Map(), cleanup = [], mounted = [], revoked = []
  const context = { nextTick, ref, navigator: { mediaDevices }, HTMLMediaElement: class {},
    onMounted: callback => mounted.push(callback), onBeforeUnmount: callback => cleanup.push(callback),
    loadAudioDevices: async () => ({ available: true, outputs: [], output_device_id: 'default' }), saveAudioOutput: async id => ({ output_device_id: id }),
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    window: { dispatchEvent: event => events.push(event) }, CustomEvent: class { constructor(name, options) { this.name = name; this.detail = options.detail } },
    URL: { createObjectURL: () => 'blob:synthetic', revokeObjectURL: url => revoked.push(url) }, Blob,
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 2, clearInterval() {},
    MediaRecorder: class { constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm' } start() { this.state = 'recording' } stop() { this.state = 'inactive'; this.onstop() } },
  }
  vm.runInNewContext(script + '\nresult = {checkDevices, record, stopRecording, inputDeviceId, outputDeviceId, mode, enumerate, save, recording, error, message, audioUrl};', context)
  return { api: context.result, mounted, cleanup, storage, events, revoked }
}

test('设备页挂载只查询正式输出，权限拒绝后仍可重新测试', async () => {
  let requested = 0
  const { api, mounted } = setup({ addEventListener() {}, getUserMedia: async () => { requested++; throw Object.assign(new Error('denied'), { name: 'NotAllowedError' }) } })
  mounted.forEach(callback => callback())
  assert.equal(requested, 0)
  await api.checkDevices(); await api.checkDevices()
  assert.equal(requested, 2)
  assert.match(api.error.value, /权限被拒绝/)
})

test('录音结束与组件卸载停止所有麦克风轨道并回收本地音频', async () => {
  let stopped = 0
  const track = { stop() { stopped++ } }
  const { api, cleanup, revoked } = setup({ removeEventListener() {}, getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }) })
  await api.record()
  assert.equal(api.recording.value, true)
  api.stopRecording(); await nextTick()
  assert.equal(stopped, 1)
  assert.equal(api.audioUrl.value, 'blob:synthetic')
  assert.match(api.message.value, /录音已完成/)
  cleanup.forEach(callback => callback())
  assert.deepEqual(revoked, ['blob:synthetic'])
})

test('麦克风断开时回退系统默认并通知通话层，不将中断录音当成成功', async () => {
  const track = { stop() {} }
  const { api, storage, events } = setup({ getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }), enumerateDevices: async () => [] })
  api.inputDeviceId.value = 'removed-mic'
  await api.record()
  await api.enumerate()
  assert.equal(api.recording.value, false)
  assert.equal(api.inputDeviceId.value, '')
  assert.equal(storage.get('mio.audio.input'), '')
  assert.equal(events.at(-1).detail.inputDeviceId, '')
  assert.match(api.message.value, /断开|中断/)
})
