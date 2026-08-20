import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const settingsSource = fs.readFileSync(new URL('./components/SettingsPage.vue', import.meta.url), 'utf8')
const voiceSource = fs.readFileSync(new URL('./components/VoiceSettingsPanel.vue', import.meta.url), 'utf8')
const dependencySource = fs.readFileSync(new URL('./components/DependencyCenter.vue', import.meta.url), 'utf8')

test('system audio is presented as part of visual observation', () => {
  assert.match(settingsSource, /随视觉观察同时开启和停止/)
  assert.doesNotMatch(settingsSource, /v-model="companionStatus\.pet\.settings\.screen_audio_enabled"/)
})

test('Genie mode shows the actual Mio ONNX model instead of legacy weight selectors', () => {
  assert.match(voiceSource, /runtime\.model_dir/)
  assert.match(voiceSource, /runtime\.model_ready/)
  assert.match(voiceSource, /runtime\.local_voice_runtime === 'genie'/)
})

test('ready dependencies clear stale progress state', () => {
  assert.match(dependencySource, /progress\.value = nextProgress/)
  assert.match(dependencySource, /\['ready', 'configured'\]\.includes\(item\.status\)/)
})
