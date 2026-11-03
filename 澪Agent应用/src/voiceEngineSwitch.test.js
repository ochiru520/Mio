import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { parse, compileScript } from '@vue/compiler-sfc'
import { computed, effectScope, reactive, ref, watch } from 'vue'
import { useCompanion } from './composables/useCompanion.js'
import { createCompanionState } from './state/companionState.js'
import { createVoiceProfile, removeVoiceProfile } from './voiceProfiles.js'
import { getVoiceModelGuide } from './voiceModelCatalog.js'
import { voiceRuntimeIdentity } from './voiceRuntimeIdentity.js'
import { submitVoiceTest, submitVoiceWeight } from './services/companionApi.js'

function panelFixture() {
  const source = readFileSync(new URL('./components/VoiceSettingsPanel.vue', import.meta.url), 'utf8')
  const script = compileScript(parse(source).descriptor, { id: 'voice-engine-behavior' }).content
  const context = { computed, ref, watch, createVoiceProfile, removeVoiceProfile, getVoiceModelGuide, voiceRuntimeIdentity, SelectField: {}, RealtimeVoiceModal: {}, component: null }
  vm.runInNewContext(script.replace(/^import .*$/gm, '').replace('export default', 'component ='), context)
  const status = reactive(createCompanionState().companionStatus.value)
  status.voice_runtime = { engine: 'gpt_sovits', local_voice_runtime: 'genie', service_running: true, model_version: 'v2', cloud_tts: { configured: true } }
  const events = []
  const scope = effectScope()
  const panel = scope.run(() => context.component.setup({ status, modelOptions: [], busy: false, rangeInputStyle: () => ({}) }, {
    expose() {}, emit: (...args) => events.push(args),
  }))
  return { status, panel, events, stop: () => scope.stop() }
}

test('switching voice engines preserves old profiles and never borrows the previous engine online state', () => {
  const { status, panel, stop } = panelFixture()
  try {
    const savedProfiles = JSON.stringify(status.pet.settings.voice_profiles)
    assert.equal(panel.currentServiceRunning.value, true)
    for (const engine of ['indextts2', 'gpt_sovits_v5', 'aniflive', 'cloud']) {
      panel.selectEngine(engine)
      assert.equal(status.pet.settings.voice_engine, engine)
      assert.equal(panel.runtimeMatchesEngine.value, false)
      assert.equal(panel.currentServiceRunning.value, false)
      assert.equal(panel.serviceVersion.value, '')
      assert.equal(JSON.stringify(status.pet.settings.voice_profiles), savedProfiles)
    }
    panel.openRealtime()
    assert.equal(panel.realtimeVisible.value, false)
    status.voice_runtime = { engine: 'aniflive', service_running: true, model_version: 'v2ProPlus' }
    panel.selectEngine('aniflive')
    assert.equal(panel.currentServiceRunning.value, true)
    assert.equal(panel.serviceVersion.value, 'v2ProPlus')
  } finally { stop() }
})

test('actual voice identity separates Anif runtime from the GPT-SoVITS model family', () => {
  const settings = { voice_profiles: { mio: { name: 'Mio 专属音色' } } }
  const anif = voiceRuntimeIdentity({
    engine: 'aniflive', service_running: true, model_version: 'GPT-SoVITS V2ProPlus',
    reference_name: 'default',
  }, settings)
  assert.equal(anif.engine, 'AnifLive 加速引擎')
  assert.equal(anif.model, 'GPT-SoVITS V2ProPlus')
  assert.equal(anif.voice, 'default')
  assert.match(anif.source, /不同的模型包/)
  assert.doesNotMatch(anif.source, /原 Mio V2 微调权重，由 AnifLive 加速/)
  const mio = voiceRuntimeIdentity({
    engine: 'gpt_sovits', local_voice_runtime: 'mio_v2', service_running: true,
    model_version: 'v2', active_profile_id: 'mio',
  }, settings)
  assert.equal(mio.engine, 'GPT-SoVITS 原生引擎')
  assert.equal(mio.model, 'GPT-SoVITS V2')
  assert.equal(mio.voice, 'Mio 专属音色')
  assert.match(mio.source, /原生引擎流式生成/)
  assert.equal(voiceRuntimeIdentity({ engine: 'aniflive', service_running: false }, settings).model, '未加载')
})

test('Anif original Mio label requires confirmed V2 model and loaded original weights', () => {
  const runtime = { engine: 'aniflive', service_running: true, model_version: 'GPT-SoVITS V2',
    active_profile_id: 'mio', active_weights: { gpt: 'D:/voices/mio.ckpt', sovits: 'D:/voices/mio.pth' } }
  assert.match(voiceRuntimeIdentity(runtime).source, /尚未确认/)
  runtime.original_mio_weights_loaded = true
  assert.match(voiceRuntimeIdentity(runtime).source, /尚未确认/)
  runtime.weight_sha256 = { gpt: 'a'.repeat(64), sovits: 'b'.repeat(64) }
  assert.equal(voiceRuntimeIdentity(runtime).source, '原 Mio V2 微调权重，由 AnifLive 加速。')
  assert.equal(voiceRuntimeIdentity(runtime).voice, '原 Mio 音色')
  delete runtime.weight_sha256.sovits
  assert.match(voiceRuntimeIdentity(runtime).source, /尚未确认/)
  runtime.weight_sha256.sovits = 'b'.repeat(64)
  runtime.model_version = 'GPT-SoVITS V2ProPlus'
  assert.match(voiceRuntimeIdentity(runtime).source, /不同的模型包/)
})

test('switching draft engine keeps actual running engine and voice visible until runtime changes', () => {
  const { status, panel, stop } = panelFixture()
  try {
    assert.equal(panel.actualVoice.value.engine, 'Genie ONNX')
    panel.selectEngine('aniflive')
    assert.equal(panel.runtimeMatchesEngine.value, false)
    assert.equal(panel.actualVoice.value.engine, 'Genie ONNX')
    status.voice_runtime = { engine: 'aniflive', service_running: true, model_version: 'v2ProPlus', reference_name: 'default' }
    assert.equal(panel.actualVoice.value.engine, 'AnifLive 加速引擎')
    assert.equal(panel.actualVoice.value.model, 'GPT-SoVITS V2ProPlus')
    assert.equal(panel.actualVoice.value.voice, 'default')
  } finally { stop() }
})

test('status refresh adopts externally saved voice choice without discarding unsaved volume draft', async () => {
  const state = createCompanionState()
  state.companionStatus.value.pet.settings.voice_engine = 'aniflive'
  state.companionStatus.value.pet.settings.local_voice_runtime = 'mio_v2'
  state.companionStatus.value.pet.settings.voice_volume = 70
  const baseline = { voice_engine: 'aniflive', local_voice_runtime: 'mio_v2', voice_volume: 85 }
  const saved = ref({ voice: JSON.stringify(baseline), pet: JSON.stringify(baseline) })
  const next = structuredClone(JSON.parse(JSON.stringify(state.companionStatus.value)))
  next.pet.settings.voice_engine = 'gpt_sovits'
  next.pet.settings.local_voice_runtime = 'mio_v2'
  next.voice_runtime = { engine: 'gpt_sovits', local_voice_runtime: 'mio_v2', service_running: true, model_version: 'v2' }
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify(next), { status: 200, headers: { 'Content-Type': 'application/json' } })
  try {
    const deps = {
      ...state, savedCompanionSettings: saved, companionSettingKeys: { voice: Object.keys(baseline) },
      companionStatusReady: ref(true), companionStatusError: ref(''), errorMessage: ref(''),
      companionSettingsSnapshot: () => '',
    }
    await useCompanion(deps).loadCompanionStatus({ quiet: true, preserveSettings: true })
    assert.equal(state.companionStatus.value.pet.settings.voice_engine, 'gpt_sovits')
    assert.equal(state.companionStatus.value.pet.settings.voice_volume, 70)
    assert.equal(JSON.parse(saved.value.voice).voice_engine, 'gpt_sovits')
  } finally { globalThis.fetch = originalFetch }
})

test('status refresh keeps the user selected voice engine draft', async () => {
  const state = createCompanionState()
  state.companionStatus.value.pet.settings.voice_engine = 'aniflive'
  const saved = ref({ voice: JSON.stringify({ voice_engine: 'gpt_sovits' }) })
  const next = structuredClone(JSON.parse(JSON.stringify(state.companionStatus.value)))
  next.pet.settings.voice_engine = 'gpt_sovits'
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify(next), { status: 200, headers: { 'Content-Type': 'application/json' } })
  try {
    const deps = { ...state, savedCompanionSettings: saved, companionSettingKeys: { voice: ['voice_engine'] },
      companionStatusReady: ref(true), companionStatusError: ref(''), errorMessage: ref(''), companionSettingsSnapshot: () => '' }
    await useCompanion(deps).loadCompanionStatus({ quiet: true, preserveSettings: true })
    assert.equal(state.companionStatus.value.pet.settings.voice_engine, 'aniflive')
    assert.equal(JSON.parse(saved.value.voice).voice_engine, 'gpt_sovits')
  } finally { globalThis.fetch = originalFetch }
})

test('the same comparison text is emitted for each engine and new engines do not change the selected legacy voice', () => {
  const { status, panel, events, stop } = panelFixture()
  try {
    panel.testText.value = '  同一段文字测试首音速度。  '
    const defaultId = status.pet.settings.default_voice_profile_id
    for (const engine of ['indextts2', 'gpt_sovits_v5', 'aniflive']) {
      panel.selectEngine(engine)
      panel.testCurrentEngine()
      assert.equal(events.at(-1)[0], 'test')
      assert.equal(events.at(-1)[1], '')
      assert.equal(events.at(-1)[2].text, '同一段文字测试首音速度。')
      assert.equal(status.pet.settings.default_voice_profile_id, defaultId)
    }
    panel.testText.value = '   '
    const count = events.length
    panel.testCurrentEngine()
    assert.equal(events.length, count)
  } finally { stop() }
})

test('voice test transport keeps caller cancellation and accepts a comparison payload without breaking old calls', async () => {
  const calls = []
  const transport = async (url, options) => { calls.push({ url, options }); return { spoken: true } }
  const signal = new AbortController().signal
  await submitVoiceTest({ signal }, transport)
  await submitVoiceTest({ signal }, transport, { text: '同一句话' })
  assert.deepEqual(calls.map(call => JSON.parse(call.options.body)), [{}, { text: '同一句话' }])
  assert.equal(calls[1].url, '/api/companion/voice/test')
  assert.equal(calls[1].options.signal, signal)
})

function companionFixture({ saveFails = false, spoken = true } = {}) {
  const state = createCompanionState()
  state.companionStatusReady.value = true
  const calls = [], feedback = []
  const deps = {
    ...state, errorMessage: ref(''), savedCompanionSettings: ref({}), companionSettingKeys: { voice: ['voice_engine'], pet: ['voice_engine'] },
    activeSettingsItem: ref({ label: '语音' }), companionSettingsSnapshot: () => '{}',
    showSettingsFeedback: (...args) => feedback.push(args),
    request: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) })
      if (url.endsWith('/settings')) {
        if (saveFails) throw new Error('保存失败')
        return state.companionStatus.value
      }
      return { spoken, last_error: spoken ? '' : '音频设备不可用' }
    },
  }
  // Status refresh is already covered elsewhere; isolate this test from the global fetch transport.
  deps.companionStatusLoading = true
  return { deps, calls, feedback, api: useCompanion(deps) }
}

test('comparison saves the selected engine before synthesis and waits for actual playback success', async () => {
  const { deps, calls, feedback, api } = companionFixture()
  deps.companionStatus.value.pet.settings.voice_engine = 'aniflive'
  await api.testCompanionVoiceProfile('', { text: '  同文对比  ' })
  assert.equal(calls[0].url, '/api/companion/settings')
  assert.deepEqual(calls[0].body, { voice_engine: 'aniflive' })
  assert.equal(calls[1].url, '/api/companion/voice/test')
  assert.deepEqual(calls[1].body, { text: '同文对比' })
  assert.match(feedback.at(-1)[2], /试听已完成/)
  assert.equal(deps.companionBusy.value, '')
})

test('failed save prevents synthesis and spoken false produces a visible failure instead of a success message', async () => {
  const failedSave = companionFixture({ saveFails: true })
  await failedSave.api.testCompanionVoiceProfile('', { text: '不会播放' })
  assert.equal(failedSave.calls.length, 1)
  assert.equal(failedSave.feedback.at(-1)[1], 'error')
  const failedPlayback = companionFixture({ spoken: false })
  await failedPlayback.api.testCompanionVoiceProfile('', { text: '无法播放' })
  assert.equal(failedPlayback.calls.length, 2)
  assert.equal(failedPlayback.feedback.at(-1)[1], 'error')
  assert.match(failedPlayback.deps.errorMessage.value, /音频设备不可用/)
  assert.equal(failedPlayback.deps.companionBusy.value, '')
})

test('a queued-only or old server response cannot claim that the comparison audio was played', async () => {
  const { deps, feedback, api } = companionFixture()
  deps.request = async () => ({ queued: true })
  await api.testCompanionVoice({ text: '还没有播放' })
  assert.equal(feedback.at(-1)[1], 'error')
  assert.match(deps.errorMessage.value, /未确认试听播放完成/)
})

test('starting a new engine saves its configuration first and a failed save prevents launch', async () => {
  const ready = companionFixture()
  ready.deps.companionStatus.value.pet.settings.voice_engine = 'indextts2'
  await ready.api.controlVoiceRuntime('start')
  assert.equal(ready.calls[0].url, '/api/companion/settings')
  assert.deepEqual(ready.calls[0].body, { voice_engine: 'indextts2' })
  assert.equal(ready.calls[1].url, '/api/companion/voice/runtime/start')
  assert.equal(ready.deps.companionBusy.value, '')
  const failed = companionFixture({ saveFails: true })
  await failed.api.controlVoiceRuntime('start')
  assert.equal(failed.calls.length, 1)
  assert.match(failed.deps.errorMessage.value, /保存失败/)
})

test('stopping an owned engine does not persist unrelated settings drafts', async () => {
  const ready = companionFixture()
  await ready.api.controlVoiceRuntime('stop')
  assert.equal(ready.calls.length, 1)
  assert.equal(ready.calls[0].url, '/api/companion/voice/runtime/stop')
})


test('switching the original voice runtime does not borrow stale Genie readiness', () => {
  const { status, panel, stop } = panelFixture()
  try {
    assert.equal(panel.currentServiceRunning.value, true)
    status.pet.settings.local_voice_runtime = 'mio_v2'
    assert.equal(panel.runtimeMatchesEngine.value, false)
    assert.equal(panel.currentServiceRunning.value, false)
    status.pet.settings.voice_profiles.mio.gpt_sovits_gpt_weights = 'D:/test/a.ckpt'
    status.pet.settings.voice_profiles.mio.gpt_sovits_sovits_weights = 'D:/test/a.pth'
    status.voice_runtime = { engine: 'gpt_sovits', local_voice_runtime: 'mio_v2', service_running: true, model_version: 'v2', active_profile_id: 'mio', active_reference_audio: status.pet.settings.voice_profiles.mio.gpt_sovits_ref_audio, verified_reference_text: status.pet.settings.voice_profiles.mio.gpt_sovits_prompt_text, active_reference_language: status.pet.settings.voice_profiles.mio.gpt_sovits_prompt_language, active_weights: { gpt: 'D:/test/a.ckpt', sovits: 'D:/test/a.pth' } }
    assert.equal(panel.currentServiceRunning.value, true)
  } finally { stop() }
})


test('self trained voice creation preserves existing voices and explicitly selects a complete voice for loading', () => {
  const { status, panel, events, stop } = panelFixture()
  try {
    const original = JSON.stringify(status.pet.settings.voice_profiles.mio)
    panel.newProfileName.value = '我的自训音色'
    panel.addVoiceProfile()
    assert.equal(status.pet.settings.local_voice_runtime, 'mio_v2')
    assert.equal(panel.selectedProfile.value.name, '我的自训音色')
    assert.equal(panel.selectedProfile.value.use_emotion_references, false)
    assert.equal(status.pet.settings.default_voice_profile_id, 'mio')
    assert.equal(panel.missingVoiceFields.value.length, 4)
    assert.equal(JSON.stringify(status.pet.settings.voice_profiles.mio), original)
    panel.selectedProfile.value.gpt_sovits_gpt_weights = 'D:/voice/custom.ckpt'
    panel.selectedProfile.value.gpt_sovits_sovits_weights = 'D:/voice/custom.pth'
    panel.selectedProfile.value.gpt_sovits_ref_audio = 'D:/voice/reference.wav'
    panel.selectedProfile.value.gpt_sovits_prompt_text = '你好。'
    assert.equal(panel.missingVoiceFields.value.length, 0)
    panel.startSelectedVoice()
    assert.equal(status.pet.settings.default_voice_profile_id, panel.selectedProfileId.value)
    assert.deepEqual(events.at(-1), ['control-runtime', 'start'])
  } finally { stop() }
})

test('model sources follow engine and native runtime selection without requiring model downloads', () => {
  const { status, panel, stop } = panelFixture()
  try {
    status.pet.settings.local_voice_runtime = 'mio_v2'
    assert.match(panel.modelGuide.value.title, /V2/)
    assert.ok(panel.modelGuide.value.links.some(link => link.url.includes('huggingface.co/lj1995/')))
    status.pet.settings.local_voice_runtime = 'genie'
    assert.match(panel.modelGuide.value.title, /ONNX/)
    panel.selectEngine('aniflive')
    assert.match(panel.modelGuide.value.requirements, /GPT-SoVITS V2.*参考音频.*NVIDIA/)
    assert.match(panel.modelGuide.value.compatibility, /V2 专用.*自训 V2/)
    assert.match(panel.modelGuide.value.compatibility, /Genie.*V2ProPlus.*v5.*不能混用/)
    assert.match(panel.modelGuide.value.compatibility, /实验.*实际身份/)
    assert.match(panel.modelGuide.value.setup, /模型与功能.*安装面板.*选择音色/)
    assert.match(panel.modelGuide.value.setup, /本机编译.*中日试听.*点击使用/)
    assert.match(panel.modelGuide.value.setup, /跳过.*取消/)
    panel.selectEngine('indextts2')
    status.pet.settings.indextts2_version = '2'
    assert.equal(panel.modelGuide.value.title, 'IndexTTS 2')
    panel.selectEngine('cloud')
    assert.match(panel.modelGuide.value.requirements, /无需下载/)
  } finally { stop() }
})

test('weight transport sends the raw File and encodes names without losing abort control', async () => {
  const calls = [], file = new File(['weight'], '中文 voice.ckpt'), signal = new AbortController().signal
  await submitVoiceWeight('voice/one', 'gpt', file, { signal }, async (...args) => { calls.push(args); return {} })
  assert.equal(calls[0][0], '/api/companion/voice/profiles/voice%2Fone/weights/gpt?filename=%E4%B8%AD%E6%96%87%20voice.ckpt')
  assert.equal(calls[0][1].body, file)
  assert.equal(calls[0][1].signal, signal)
  assert.equal(calls[0][1].headers['Content-Type'], 'application/octet-stream')
})

test('weight import saves voice creation first then adopts server paths and leaves other voices intact', async () => {
  const f = companionFixture(), original = JSON.stringify(f.deps.companionStatus.value.pet.settings.voice_profiles.mio)
  const created = createVoiceProfile(f.deps.companionStatus.value.pet.settings.voice_profiles, '自训音色', 77)
  f.deps.companionStatus.value.pet.settings.voice_profiles[created.id] = created.profile
  f.deps.companionSettingKeys.voice = ['voice_engine', 'voice_profiles']
  f.deps.request = async (url, options) => {
    f.calls.push({ url, body: options.body })
    const next = structuredClone(JSON.parse(JSON.stringify(f.deps.companionStatus.value)))
    if (url.includes('/weights/')) next.pet.settings.voice_profiles[created.id].gpt_sovits_gpt_weights = 'D:/Data/音色权重/upload.ckpt'
    return next
  }
  const event = { target: { files: [new File(['weight'], 'custom.ckpt')], value: 'selected' } }
  await f.api.uploadVoiceWeight(event, created.id, 'gpt')
  assert.equal(f.calls[0].url, '/api/companion/settings')
  assert.ok(JSON.parse(f.calls[0].body).voice_profiles[created.id])
  assert.ok(f.calls[1].body instanceof File)
  assert.equal(f.deps.companionStatus.value.pet.settings.voice_profiles[created.id].gpt_sovits_gpt_weights, 'D:/Data/音色权重/upload.ckpt')
  assert.equal(JSON.stringify(f.deps.companionStatus.value.pet.settings.voice_profiles.mio), original)
  assert.equal(event.target.value, '')
  assert.equal(f.deps.companionBusy.value, '')
})

test('invalid or failed weight upload never overwrites the selected training path', async () => {
  const f = companionFixture(), before = JSON.stringify(f.deps.companionStatus.value.pet.settings.voice_profiles)
  await f.api.uploadVoiceWeight({ target: { files: [new File(['bad'], 'wrong.pth')], value: '' } }, 'mio', 'gpt')
  assert.equal(f.calls.length, 0)
  assert.equal(f.feedback.at(-1)[1], 'error')
  f.deps.request = async (url) => { if (url.endsWith('/settings')) return f.deps.companionStatus.value; throw new Error('磁盘空间不足') }
  await f.api.uploadVoiceWeight({ target: { files: [new File(['weight'], 'a.ckpt')], value: '' } }, 'mio', 'gpt')
  assert.equal(JSON.stringify(f.deps.companionStatus.value.pet.settings.voice_profiles), before)
  assert.match(f.deps.errorMessage.value, /磁盘空间不足/)
  assert.equal(f.deps.companionBusy.value, '')
})

test('native readiness checks selected identity and reference edits; failed voice can restore last success', () => {
  const { status, panel, events, stop } = panelFixture()
  try {
    const settings = status.pet.settings, profile = settings.voice_profiles.mio
    settings.local_voice_runtime = 'mio_v2'
    profile.gpt_sovits_gpt_weights = 'D:/a.ckpt'; profile.gpt_sovits_sovits_weights = 'D:/a.pth'
    status.voice_runtime = { engine: 'gpt_sovits', local_voice_runtime: 'mio_v2', service_running: true, active_profile_id: 'mio', active_weights: { gpt: 'D:/a.ckpt', sovits: 'D:/a.pth' }, active_reference_audio: profile.gpt_sovits_ref_audio, verified_reference_text: profile.gpt_sovits_prompt_text, active_reference_language: profile.gpt_sovits_prompt_language, last_successful_profile_id: 'mio' }
    assert.equal(panel.currentServiceRunning.value, true)
    profile.gpt_sovits_prompt_text += '修改'
    assert.equal(panel.currentServiceRunning.value, false)
    panel.newProfileName.value = '加载失败的音色'; panel.addVoiceProfile(); panel.makeSelectedDefault()
    assert.equal(panel.canRestoreVoice.value, true)
    panel.restorePreviousVoice()
    assert.equal(panel.selectedProfileId.value, 'mio')
    assert.equal(settings.default_voice_profile_id, 'mio')
    assert.deepEqual(events.at(-1), ['control-runtime', 'start'])
  } finally { stop() }
})
